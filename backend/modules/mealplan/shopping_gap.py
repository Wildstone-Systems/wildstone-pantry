"""The meal plan's shopping bridge (#31): what to buy for the meals planned ahead.

- Only ``planned`` entries dated **today onward** inside the window count.
- Recipe entries use planning's ``recipe_availability`` scaled to the entry's
  pinned servings, so canonical pooling and unit conversion match the rest of
  the app. Item entries need ``servings`` × "each" of that item.
- Needs are summed per ingredient + unit across meals. Stock is pooled per
  ingredient (canonical key) and shared out once across that ingredient's
  unit lines, so two units never both spend the same stock.
- A line we can't measure (no unit bridge) is shown as ``check``, never
  silently dropped. Always-skipped ingredients (staples) are listed apart.
- Pushing is on request only and goes through ``add_entry_once``, so it never
  duplicates a pending list entry.
"""

from __future__ import annotations

import logging
from datetime import date, timedelta
from decimal import Decimal
from uuid import UUID

from sqlalchemy.orm import Session

from modules.catalog.services.items import get_item
from modules.inventory.services.feed import household_stock
from modules.mealplan import repository
from modules.mealplan.models import MealPlanExcludedItem
from modules.planning.services.recipes import get_recipe, recipe_availability
from modules.shopping.services.shopping_list import add_entry_once

logger = logging.getLogger(__name__)

_CENT = Decimal("0.01")


class ShoppingGapInputError(ValueError):
    """The request is well-formed but can't be carried out as asked."""


def _key(canonical: str | None, item_id) -> str:
    return f"canon:{canonical}" if canonical else f"item:{item_id}"


def _amount(value: Decimal) -> Decimal:
    """Two decimal places, trailing zeros dropped (``1.50`` → ``1.5``)."""
    rounded = value.quantize(_CENT)
    # normalize() alone would turn 10 into 1E+1.
    return rounded.quantize(Decimal(1)) if rounded == rounded.to_integral() else rounded.normalize()


def _line(lines: dict, *, key: str, unit: str, label: str) -> dict:
    # on_hand is None until a source reports it: the pooled stock for this
    # ingredient, expressed in this line's unit.
    return lines.setdefault(
        (key, unit),
        {"key": key, "label": label, "unit": unit, "needed": Decimal("0"), "on_hand": None,
         "measurable": True, "meals": []},
    )


def _note_meal(line: dict, meal: str) -> None:
    if meal not in line["meals"]:
        line["meals"].append(meal)


def _each_stock_by_key(db: Session) -> dict[str, Decimal | None]:
    """Household stock counted in "each", pooled per ingredient key (canonical
    when the item has one). None for a key with stock we can't count in each."""
    pooled: dict[str, Decimal | None] = {}
    for row in household_stock(db):
        item = get_item(db, row["item_id"])
        key = _key(item.canonical_ingredient if item else None, row["item_id"])
        if row["unit"] != "each" or pooled.get(key, Decimal("0")) is None:
            pooled[key] = None
        else:
            pooled[key] = pooled.get(key, Decimal("0")) + Decimal(row["quantity"])
    return pooled


def _allocate(lines: list[dict]) -> None:
    """Share one ingredient's stock across its unit lines, so stock is used once.

    Every measurable line reports the same stock in its own unit, so the share
    still unused is a unit-free fraction: 100 g and 0.1 kg of flour with 100 g
    on hand → the first line uses it all, the second buys 0.1 kg.
    """
    remaining = Decimal("1")
    for line in lines:
        if not line["measurable"]:
            continue
        on_hand = line["on_hand"] or Decimal("0")
        available = on_hand * remaining
        used = min(line["needed"], available)
        line["available"] = available
        line["to_buy"] = line["needed"] - used
        if on_hand > 0:
            remaining = max(Decimal("0"), remaining - used / on_hand)


def compute(db: Session, *, start: date, days: int, today: date) -> dict:
    end = start + timedelta(days=days)
    window_start = max(start, today)
    lines: dict[tuple[str, str], dict] = {}
    each_stock = None

    entries = [] if window_start >= end else repository.list_entries(db, start=window_start, end=end)
    for entry in entries:
        if entry.status != "planned":
            continue
        if entry.recipe_id is not None:
            recipe = get_recipe(db, entry.recipe_id)
            if recipe is None:
                continue  # deleted recipe: nothing left to shop for
            rows = recipe_availability(db, recipe.id, servings_made=Decimal(entry.servings))["ingredients"]
            for row in rows:
                if row["status"] == "optional":
                    continue
                line = _line(lines, key=_key(row["canonical_ingredient"], row["item_id"]), unit=row["unit"],
                             label=row["item_name"])
                line["needed"] += Decimal(row["quantity_required"])
                if row["status"] == "cant_convert" or row["on_hand_recipe_unit"] is None:
                    line["measurable"] = False
                else:
                    # Pooled on-hand for this ingredient; the same for every meal.
                    line["on_hand"] = Decimal(row["on_hand_recipe_unit"])
                _note_meal(line, recipe.name)
        elif entry.item_id is not None:
            item = get_item(db, entry.item_id)
            if item is None:
                continue
            if each_stock is None:
                each_stock = _each_stock_by_key(db)
            key = _key(item.canonical_ingredient, item.id)
            line = _line(lines, key=key, unit="each", label=item.name)
            line["needed"] += Decimal(entry.servings)
            pooled = each_stock.get(key, Decimal("0"))
            if pooled is None:
                line["measurable"] = False
            elif line["on_hand"] is None:
                line["on_hand"] = pooled
            _note_meal(line, item.name)
        # Free-text entries ("Takeout") need nothing from the shop.

    by_key: dict[str, list[dict]] = {}
    for line in lines.values():
        by_key.setdefault(line["key"], []).append(line)
    for unit_lines in by_key.values():
        _allocate(unit_lines)

    excluded = {row.key: row for row in repository.list_excluded(db)}
    out_lines: list[dict] = []
    for line in lines.values():
        if line["key"] in excluded:
            continue
        if line["measurable"]:
            if line["to_buy"] <= 0:
                continue
            status, to_buy, on_hand = "short", _amount(line["to_buy"]), _amount(line["available"])
        else:
            status, to_buy, on_hand = "check", None, None
        out_lines.append(
            {
                "key": line["key"],
                "label": line["label"],
                "unit": line["unit"],
                "needed": _amount(line["needed"]),
                # What this line could draw on after other units of the same
                # ingredient took their share.
                "on_hand": on_hand,
                "to_buy": to_buy,
                "status": status,
                "meals": line["meals"],
            }
        )
    out_lines.sort(key=lambda r: (r["status"] != "short", r["label"].lower(), r["unit"]))
    return {
        "start": window_start if window_start < end else end,
        "end": end,
        "lines": out_lines,
        "excluded": [{"id": row.id, "key": row.key, "label": row.label} for row in excluded.values()],
    }


def push(db: Session, *, start: date, days: int, today: date, keys: list[str]) -> dict:
    """Add the chosen gap lines to the shopping list. Recomputed server-side, so
    a stale preview can't push something no longer needed.

    One list entry per ingredient: its unit lines are combined into one note
    ("200 g + 0.1 kg"), because a repeat-safe add would drop the later ones.
    """
    wanted = set(keys)
    gap = compute(db, start=start, days=days, today=today)
    chosen: dict[str, list[dict]] = {}
    for line in gap["lines"]:
        if line["key"] in wanted:
            chosen.setdefault(line["key"], []).append(line)

    added: list[str] = []
    already: list[str] = []
    for unit_lines in chosen.values():
        amounts = " + ".join(
            f"{line['to_buy']} {line['unit']}" if line["to_buy"] is not None else f"? {line['unit']} (check)"
            for line in unit_lines
        )
        meals: list[str] = []
        for line in unit_lines:
            meals.extend(m for m in line["meals"] if m not in meals)
        note = f"Meal plan: {amounts} ({', '.join(meals)})"[:500]
        entry, was_added = add_entry_once(db, name=unit_lines[0]["label"], source="meal_plan", note=note)
        (added if was_added else already).append(entry["name"])
    logger.info("meal plan shopping push: %d added, %d already on the list", len(added), len(already))
    return {"added": added, "already_on_list": already}


# --- always-skipped ingredients --------------------------------------------------


def _valid_key(key: str) -> str:
    key = key.strip()
    kind, _, rest = key.partition(":")
    if kind == "canon" and rest.strip():
        return key
    if kind == "item":
        try:
            UUID(rest)
            return key
        except ValueError:
            pass
    raise ShoppingGapInputError("key must be canon:<ingredient> or item:<item id>")


def exclude(db: Session, *, key: str, label: str) -> dict:
    key = _valid_key(key)
    label = label.strip()
    if not label:
        raise ShoppingGapInputError("label is required")
    row = repository.get_excluded_by_key(db, key)
    if row is None:
        row = MealPlanExcludedItem(key=key, label=label[:200])
        db.add(row)
        db.commit()
        db.refresh(row)
    return {"id": row.id, "key": row.key, "label": row.label}


def list_excluded(db: Session) -> list[dict]:
    return [{"id": row.id, "key": row.key, "label": row.label} for row in repository.list_excluded(db)]


def unexclude(db: Session, excluded_id: UUID) -> None:
    row = repository.get_excluded(db, excluded_id)
    if row is None:
        raise LookupError("Not on the always-skip list")
    db.delete(row)
    db.commit()
