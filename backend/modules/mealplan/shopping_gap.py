"""The meal plan's shopping bridge (#31): what to buy for the meals planned ahead.

- Only ``planned`` entries dated **today onward** inside the window count.
- Recipe entries use planning's ``recipe_availability`` scaled to the entry's
  pinned servings, so canonical pooling and unit conversion match the rest of
  the app. Item entries need ``servings`` × "each" of that item.
- Needs are summed per ingredient + unit across meals; on-hand is subtracted
  once (it is the same stock, whichever meal uses it).
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
    return lines.setdefault(
        (key, unit),
        {"key": key, "label": label, "unit": unit, "needed": Decimal("0"), "on_hand": Decimal("0"),
         "measurable": True, "meals": []},
    )


def _note_meal(line: dict, meal: str) -> None:
    if meal not in line["meals"]:
        line["meals"].append(meal)


def compute(db: Session, *, start: date, days: int, today: date) -> dict:
    end = start + timedelta(days=days)
    window_start = max(start, today)
    lines: dict[tuple[str, str], dict] = {}
    stock_by_item = None

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
            if stock_by_item is None:
                stock_by_item = {row["item_id"]: row for row in household_stock(db)}
            line = _line(lines, key=_key(item.canonical_ingredient, item.id), unit="each", label=item.name)
            line["needed"] += Decimal(entry.servings)
            stock = stock_by_item.get(item.id)
            if stock is not None:
                if stock["unit"] == "each":
                    line["on_hand"] = Decimal(stock["quantity"])
                else:
                    line["measurable"] = False
            _note_meal(line, item.name)
        # Free-text entries ("Takeout") need nothing from the shop.

    excluded = {row.key: row for row in repository.list_excluded(db)}
    out_lines: list[dict] = []
    for line in lines.values():
        if line["key"] in excluded:
            continue
        if line["measurable"]:
            to_buy = line["needed"] - line["on_hand"]
            if to_buy <= 0:
                continue
            status = "short"
        else:
            to_buy = None
            status = "check"
        out_lines.append(
            {
                "key": line["key"],
                "label": line["label"],
                "unit": line["unit"],
                "needed": _amount(line["needed"]),
                "on_hand": _amount(line["on_hand"]) if line["measurable"] else None,
                "to_buy": _amount(to_buy) if to_buy is not None else None,
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
    a stale preview can't push something no longer needed."""
    wanted = set(keys)
    gap = compute(db, start=start, days=days, today=today)
    added: list[str] = []
    already: list[str] = []
    for line in gap["lines"]:
        if line["key"] not in wanted:
            continue
        amount = f"{line['to_buy']} {line['unit']}" if line["to_buy"] is not None else "check amount"
        note = f"Meal plan: {amount} ({', '.join(line['meals'])})"[:500]
        entry, was_added = add_entry_once(db, name=line["label"], source="meal_plan", note=note)
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
