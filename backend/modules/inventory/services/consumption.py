"""Per-eater consumption ("plate / eat") + daily calorie rollup — step 4b.

Eating is per-eater (ADR 0004 attribution): a plate is a ``consumed`` inventory
event whose ``actor`` is the household profile that ate it, carrying a **calorie
snapshot** in the event ``payload``. Snapshotting calories at plate time (rather
than recomputing from the item's nutrition later) keeps the rollup stable when an
item's ``calories_per_unit`` is edited afterwards.

Stays on the ledger exactly like the other inventory mutations
(``modules.inventory.services.inventory``): append the event + rebuild
projections in one transaction, and dual-write the legacy ``inventory`` /
``inventory_transactions`` rows so the parity backstop holds.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone as _timezone
from decimal import Decimal
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import HouseholdProfile, Inventory, InventoryTransaction, Item
from modules.catalog.services.ingredient_reference import macros_for_amount
from modules.inventory import repository
from modules.inventory.models import InventoryEvent
from modules.inventory.projector import rebuild_projections
from modules.inventory.services.inventory import (
    _get_inventory_row,
    _get_or_create_inventory_row,
    add_inventory,
    convert_amount,
    get_item,
    get_location,
    list_item_stock_fefo,
)

# Units that let a supplied ``weight_grams`` stand in for ``quantity`` directly.
# For anything else weight is informational and an explicit quantity is required.
_GRAM_UNITS = {"g", "gram", "grams"}
_KILOGRAM_UNITS = {"kg", "kilogram", "kilograms"}

# Marks a `consumed` event as the compensating entry for a deleted meal (ADR
# 0002: never a hard delete). The suffix is the ORIGINAL event's id, so
# daily_calorie_rollup can hide both the mistake and its correction from the
# displayed list while their (positive, negative) payloads still net the
# total to zero.
_MEAL_DELETE_MARKER_PREFIX = "deleted_meal:"


def _derive_quantity(quantity: Decimal | None, weight_grams: Decimal | None, unit: str) -> Decimal:
    """Resolve the consumed quantity (see ``weight_grams`` rule in the handoff).

    - An explicit ``quantity`` always wins.
    - Otherwise, if the plate ``unit`` is a weight (g / kg), map ``weight_grams``
      onto it (kg = grams / 1000).
    - Otherwise ``weight_grams`` is informational only and a quantity is required.
    """
    if quantity is not None:
        if quantity <= 0:
            raise ValueError("quantity must be greater than zero")
        return Decimal(quantity)

    normalized = unit.strip().lower()
    if weight_grams is not None and weight_grams > 0:
        if normalized in _GRAM_UNITS:
            return Decimal(weight_grams)
        if normalized in _KILOGRAM_UNITS:
            return Decimal(weight_grams) / Decimal("1000")

    raise ValueError("Provide quantity, or weight_grams with a weight unit (g/kg)")


def plate(
    db: Session,
    *,
    item_id: UUID,
    location_id: UUID | None,
    profile_id: str,
    quantity: Decimal | None = None,
    unit: str = "each",
    weight_grams: Decimal | None = None,
    note: str | None = None,
    occurred_at: datetime | None = None,
    meal_type: str | None = None,
) -> tuple[UUID, Inventory | None, Item, Decimal, Decimal | None, Decimal | None, Decimal | None, Decimal | None]:
    """Attribute food intake to a profile: consume ``item_id`` at ``location_id``
    as ``profile_id`` and snapshot the calories onto the event payload.

    ``location_id=None`` is an off-pantry plate (never in stock here — a
    gas-station snack, a restaurant meal): no ``Inventory`` row is touched, but
    the ``consumed`` ledger event is still appended with ``location_id=None`` —
    the projector already skips the balance delta for a location-less event
    (see ``projections/event_data.py``), and the calorie rollup filters purely
    on event_type + actor, not location, so it counts exactly the same.

    ``occurred_at`` defaults to now; ``edit_plate`` backdates it to the
    original plate's day so correcting an old entry doesn't move it into
    today's log.

    Raises LookupError (→404) for an unknown item/location and ValueError (→400)
    for an underivable quantity or insufficient stock.
    """
    item = get_item(db, item_id)
    if not item:
        raise LookupError("Item not found")

    row: Inventory | None = None
    qty = _derive_quantity(quantity, weight_grams, unit)
    stock_amount: Decimal | None = None
    stock_unit = unit

    if location_id is not None:
        location = get_location(db, location_id)
        if not location:
            raise LookupError("Location not found")

        # Convert into whatever unit this item is already stocked in at this
        # location (ADR 0016) — plating "170 g" of something tracked as 2
        # "each" containers must draw a fraction of a container, not compare
        # grams against a container count. The stock-unit amount is what
        # actually moves the row/ledger/lots; the original qty/unit is kept
        # for the calorie snapshot below (the amount actually eaten).
        existing = _get_inventory_row(db, item_id=item_id, location_id=location_id)
        stock_unit = existing.unit if (existing and Decimal(existing.quantity) > 0) else unit
        converted = convert_amount(db, item, qty, from_unit=unit, to_unit=stock_unit)
        if converted is None:
            raise ValueError(
                f"Can't plate {qty} {unit} of {item.name}: no way to convert "
                f"{unit} to the stocked unit ({stock_unit}). Set a package size, "
                f"density, or weight-per-each on the item."
            )
        stock_amount = converted

        row = _get_or_create_inventory_row(
            db, item_id=item_id, location_id=location_id, unit=stock_unit, expiration_date=None
        )
        if Decimal(row.quantity) < stock_amount:
            raise ValueError("Insufficient inventory quantity")

    # Nutrition snapshot (ADR 0011 D4). Prefer the structured per-100g figures
    # whenever the amount is convertible — they're the source of truth and keep
    # calories consistent with the protein/fat/carbs snapshotted alongside. The
    # legacy per-unit calories are only a fallback for items that can't be
    # converted (or carry no per-100g energy). Snapshotting keeps the rollup
    # stable across later nutrition edits.
    calories: Decimal | None = None
    protein_g: Decimal | None = None
    fat_g: Decimal | None = None
    carbs_g: Decimal | None = None
    macros = macros_for_amount(db, item, qty, unit)
    if macros is not None:
        protein_g = macros["protein_g"]
        fat_g = macros["fat_g"]
        carbs_g = macros["carbs_g"]
        calories = macros["calories"]
    if calories is None and item.calories_per_unit is not None:
        calories = (qty * Decimal(item.calories_per_unit)).quantize(Decimal("0.01"))

    if row is not None:
        assert stock_amount is not None
        row.quantity = Decimal(row.quantity) - stock_amount
        row.unit = stock_unit

        tx = InventoryTransaction(
            item_id=item_id,
            location_id=location_id,
            quantity_change=Decimal("0") - stock_amount,
            unit=stock_unit,
            transaction_type="remove",
            reason="plate",
            note=note,
        )
        db.add(tx)
        db.add(row)

    # Ledger: a plate is a `consumed` fact attributed to the eater, carrying the
    # calorie snapshot + weight/profile in payload for the per-profile rollup.
    # Recorded in the stock unit (not the eaten unit) so FEFO/container
    # allocation — which auto-opens a sealed container when needed — folds it
    # exactly like any other removal (see replay_lots/_consume_containers).
    event = repository.append_event(
        db,
        event_type="consumed",
        item_id=item_id,
        location_id=location_id,
        quantity=stock_amount if stock_amount is not None else qty,
        unit=stock_unit,
        source="api",
        actor=profile_id,
        reason="plate",
        note=note,
        occurred_at=occurred_at,
        payload={
            "calories": None if calories is None else float(calories),
            "protein_g": None if protein_g is None else float(protein_g),
            "fat_g": None if fat_g is None else float(fat_g),
            "carbs_g": None if carbs_g is None else float(carbs_g),
            "profile_id": profile_id,
            "weight_grams": None if weight_grams is None else float(weight_grams),
            # The amount actually eaten, in the unit the eater/recipe expressed
            # it in — kept separate from the ledger event's own quantity/unit,
            # which is in the stock unit (see the comment above) so FEFO /
            # container allocation folds it correctly. The calorie rollup below
            # prefers these for display.
            "quantity": float(qty),
            "unit": unit,
            # breakfast / lunch / dinner / snack — free-form, purely for grouping
            # a day's entries. Never affects totals.
            "meal_type": meal_type,
        },
    )
    event_id = event.event_id
    rebuild_projections(db)

    db.commit()
    if row is not None:
        db.refresh(row)
    return event_id, row, item, qty, calories, protein_g, fat_g, carbs_g


def delete_plate(db: Session, *, event_id: UUID) -> None:
    """Undo a mistaken plate ("I didn't actually have coconut oil") — never a
    hard delete (ADR 0002): restores any inventory the original decremented,
    the same way a mistaken Log-tab removal is undone (see
    ``inventory.reverse_transaction``), and appends a negative-payload
    ``consumed`` event so the calorie total nets the mistake to zero. Both the
    original and the compensating entry stay in the ledger but are hidden from
    ``daily_calorie_rollup``'s displayed list (see its ``deleted_ids`` filter).

    Raises LookupError (→404) for an unknown/non-plate event and ValueError
    (→400) if it was already deleted (or is itself a deletion's compensating
    entry, which was never a plate to undo).
    """
    event = db.execute(
        select(InventoryEvent).where(InventoryEvent.event_id == event_id).with_for_update()
    ).scalar_one_or_none()
    if event is None or event.event_type != "consumed":
        raise LookupError("Meal entry not found")
    if event.note and event.note.startswith(_MEAL_DELETE_MARKER_PREFIX):
        raise ValueError("This is itself a correction, not a plate — nothing to delete")

    marker = f"{_MEAL_DELETE_MARKER_PREFIX}{event_id}"
    already = db.execute(select(InventoryEvent).where(InventoryEvent.note == marker)).scalar_one_or_none()
    if already is not None:
        raise ValueError("This entry was already deleted")

    if event.location_id is not None:
        # event.quantity/unit are already the stock-unit amount plate()
        # decremented — restore exactly that, the same way reverse_transaction
        # re-adds a mistaken "remove" (a fresh lot, not perfect FEFO fidelity,
        # same accepted trade-off as that path).
        add_inventory(
            db,
            item_id=event.item_id,
            location_id=event.location_id,
            quantity=Decimal(event.quantity),
            unit=event.unit,
            expiration_date=None,
            reason="reversal",
            note=f"deleted_meal_restore:{event_id}",
        )

    payload = event.payload or {}

    def _neg(key: str) -> float | None:
        value = payload.get(key)
        return None if value is None else -float(value)

    repository.append_event(
        db,
        event_type="consumed",
        item_id=event.item_id,
        location_id=None,
        quantity=Decimal("0"),
        unit=event.unit,
        source="api",
        actor=event.actor,
        reason="deleted",
        note=marker,
        # Backdated to the ORIGINAL plate's day — a mistake logged yesterday
        # but deleted today must still net out of *yesterday's* total, not
        # show up as a stray negative entry in today's.
        occurred_at=event.occurred_at,
        payload={
            "calories": _neg("calories"),
            "protein_g": _neg("protein_g"),
            "fat_g": _neg("fat_g"),
            "carbs_g": _neg("carbs_g"),
            "profile_id": payload.get("profile_id"),
            "weight_grams": None,
            "quantity": 0,
            "unit": event.unit,
        },
    )
    rebuild_projections(db)
    db.commit()


def edit_plate(
    db: Session,
    *,
    event_id: UUID,
    quantity: Decimal | None = None,
    unit: str | None = None,
    weight_grams: Decimal | None = None,
) -> tuple[UUID, Inventory | None, Item, Decimal, Decimal | None, Decimal | None, Decimal | None, Decimal | None, str, str]:
    """Correct a mistaken plate's amount without a delete-then-relog round trip.

    Composes ``delete_plate`` (undo the original — restores inventory, zeroes
    the original out of the calorie total) with a fresh ``plate()`` call for
    the corrected amount, on the same item/location/profile — and backdated to
    the same day, so editing an old entry doesn't move it into today's log.
    ``quantity``/``unit`` default to the original eaten amount when omitted
    (edit just one field, e.g. only the unit).

    Raises the same errors as ``delete_plate`` for an unknown/already-deleted/
    correction event, plus whatever ``plate()`` raises for the new amount
    (e.g. insufficient stock if the edit increases a pantry-backed quantity).
    """
    event = db.execute(select(InventoryEvent).where(InventoryEvent.event_id == event_id)).scalar_one_or_none()
    if event is None or event.event_type != "consumed":
        raise LookupError("Meal entry not found")
    if event.reason == "plate_recipe":
        # A recipe meal is measured in SERVINGS, and `plate` below re-logs in an
        # item unit — there is no bridge from "1.5 servings" to grams of the
        # output. Refused up front rather than after `delete_plate` has already
        # committed the undo, which would leave the meal deleted, its stock
        # restored, and no replacement entry.
        raise ValueError("A recipe meal can't be edited by amount — delete it and log it again")

    payload = event.payload or {}
    original_quantity = payload.get("quantity")
    new_quantity = (
        quantity if quantity is not None else (Decimal(str(original_quantity)) if original_quantity is not None else None)
    )
    new_unit = unit if unit is not None else (payload.get("unit") or event.unit)
    item_id, location_id, profile_id, occurred_at = event.item_id, event.location_id, event.actor, event.occurred_at

    delete_plate(db, event_id=event_id)

    result = plate(
        db,
        item_id=item_id,
        location_id=location_id,
        profile_id=profile_id,
        quantity=new_quantity,
        unit=new_unit,
        weight_grams=weight_grams,
        occurred_at=occurred_at,
    )
    return (*result, new_unit, profile_id)


def plate_batch(db: Session, *, profile_id: str, entries: list[dict]) -> list[dict]:
    """Log several things eaten in one confirmation (a bar, a bag of chips, a soda).

    Validated as a whole BEFORE anything is written: an unknown item, an
    unconvertible unit, or insufficient stock fails the batch with nothing
    logged, rather than committing the first two and leaving you to work out
    which of five entries landed. Same plan-then-execute discipline the recipe
    cook path uses.

    An entry whose calories can't be computed is NOT a failure — it logs with
    calories null, exactly as a single plate does. The caller shows that up front
    (see ``loggable_units``); refusing here would block logging a real item
    just because its nutrition is unknown.
    """
    if not entries:
        raise ValueError("No entries to log")

    problems: list[str] = []
    prepared: list[dict] = []
    for index, entry in enumerate(entries):
        item_id = entry.get("item_id")
        item = get_item(db, item_id) if item_id else None
        if item is None:
            problems.append(f"entry {index + 1}: item not found")
            continue

        unit = entry.get("unit") or "each"
        quantity = entry.get("quantity")
        weight_grams = entry.get("weight_grams")
        try:
            qty = _derive_quantity(
                None if quantity is None else Decimal(str(quantity)),
                None if weight_grams is None else Decimal(str(weight_grams)),
                unit,
            )
        except ValueError as exc:
            problems.append(f"entry {index + 1} ({item.name}): {exc}")
            continue

        location_id = entry.get("location_id")
        if location_id is not None:
            # Same stock-unit conversion + sufficiency check plate() does, run
            # here so a shortfall is reported before ANY entry is written.
            existing = _get_inventory_row(db, item_id=item.id, location_id=location_id)
            stock_unit = existing.unit if (existing and Decimal(existing.quantity) > 0) else unit
            converted = convert_amount(db, item, qty, from_unit=unit, to_unit=stock_unit)
            if converted is None:
                problems.append(
                    f"entry {index + 1} ({item.name}): can't convert {unit} to the stocked unit ({stock_unit})"
                )
                continue
            if existing is None or Decimal(existing.quantity) < converted:
                on_hand = Decimal(existing.quantity) if existing else Decimal("0")
                problems.append(
                    f"entry {index + 1} ({item.name}): only {on_hand} {stock_unit} in stock, needs {converted}"
                )
                continue

        prepared.append(
            {
                "item_id": item.id,
                "location_id": location_id,
                "quantity": qty,
                "unit": unit,
                "weight_grams": None if weight_grams is None else Decimal(str(weight_grams)),
                "meal_type": entry.get("meal_type"),
                "note": entry.get("note"),
            }
        )

    if problems:
        raise ValueError("; ".join(problems))

    results: list[dict] = []
    for step in prepared:
        event_id, row, item, qty, calories, protein_g, fat_g, carbs_g = plate(
            db,
            item_id=step["item_id"],
            location_id=step["location_id"],
            profile_id=profile_id,
            quantity=step["quantity"],
            unit=step["unit"],
            weight_grams=step["weight_grams"],
            note=step["note"],
            meal_type=step["meal_type"],
        )
        results.append(
            {
                "event_id": event_id,
                "item_id": item.id,
                "item_name": item.name,
                "location_id": row.location_id if row is not None else None,
                "quantity": qty,
                "unit": step["unit"],
                "calories": calories,
                "protein_g": protein_g,
                "fat_g": fat_g,
                "carbs_g": carbs_g,
                "balance": row.quantity if row is not None else None,
            }
        )
    return results


def _recipe_meal_item(db: Session, recipe) -> Item:
    """The catalog item a recipe-meal is recorded against.

    Events require an item_id, but "I ate two servings of the chili" is about a
    recipe. Use the recipe's declared output item when it has one; otherwise
    find-or-create a lightweight stand-in named after the recipe (same shape as
    the USDA generic-food items — never stocked, just an identity anchor). The
    nutrition on the event comes from the RECIPE, not from this item.
    """
    if recipe.output_item_id is not None:
        item = db.get(Item, recipe.output_item_id)
        if item is not None:
            return item

    existing = db.execute(
        select(Item).where(Item.name == recipe.name, Item.source == "recipe_meal")
    ).scalars().first()
    if existing is not None:
        return existing

    from modules.catalog.services.canonical import CANONICAL_VERSION, canonical_ingredient

    item = Item(
        name=recipe.name[:200],
        source="recipe_meal",
        stock_mode="measured",
        base_unit="g",
        canonical_ingredient=canonical_ingredient(recipe.name),
        canonical_provenance="generated",
        canonical_version=CANONICAL_VERSION,
    )
    db.add(item)
    db.flush()
    return item


def plate_recipe(
    db: Session,
    *,
    recipe_id: UUID,
    profile_id: str,
    servings: Decimal,
    note: str | None = None,
    meal_type: str | None = None,
    occurred_at: datetime | None = None,
) -> tuple[UUID, Item, Decimal, dict, bool, Decimal | None, str | None, UUID | None]:
    """Log eating a portion of a recipe.

    Nutrition is computed from the recipe's own ingredients (per serving) and
    scaled by ``servings``.

    **Inventory.** Eating never re-deducts the *ingredients* — cooking already
    did that, and deducting again would double-count. But a recipe that declares
    an OUTPUT item produced a tracked lot of it, and eating that is an ordinary
    depletion of ordinary stock: without it the pulled pork is cooked, eaten, and
    still on the books forever (ADR 0025 §5). So when the recipe has an output
    item with stock on hand, this deducts the servings eaten from it, FEFO,
    container-aware — one `consumed` event carrying both the balance delta and
    the calorie snapshot, exactly as ``plate`` does.

    Everything else keeps the original stock-less behaviour: no output item, no
    ``output_quantity`` to size a serving with, or nothing stocked anywhere. That
    last case is deliberate — leftovers that were never tracked, or a dish
    someone logged after the stock ran out, must stay loggable.

    Returns (event_id, anchor_item, servings, macros, complete, deducted,
    deducted_unit, location_id) — the last three are None when nothing was
    deducted, so a caller can tell "ate it, stock went down" from "logged only".
    """
    # Lazy import: planning imports inventory at module load, so importing it
    # back at the top would cycle (same pattern as resolve_scan's cross-module
    # import in catalog).
    from modules.planning.services.recipes import get_recipe, recipe_nutrition

    recipe = get_recipe(db, recipe_id)
    if not recipe:
        raise LookupError("Recipe not found")
    if servings <= 0:
        raise ValueError("servings must be greater than zero")

    nutrition = recipe_nutrition(db, recipe_id)
    per_serving = nutrition["per_serving"]
    scale = Decimal(servings)

    def _scaled(key: str) -> Decimal | None:
        value = per_serving.get(key)
        return None if value is None else (Decimal(str(value)) * scale).quantize(Decimal("0.01"))

    macros = {
        "calories": _scaled("calories"),
        "protein_g": _scaled("protein_g"),
        "fat_g": _scaled("fat_g"),
        "carbs_g": _scaled("carbs_g"),
    }
    item = _recipe_meal_item(db, recipe)
    steps = _resolve_output_deduction(db, recipe, item, servings=scale)

    for step in steps:
        row = _get_or_create_inventory_row(
            db, item_id=item.id, location_id=step["location_id"], unit=step["unit"], expiration_date=None
        )
        row.quantity = Decimal(row.quantity) - step["quantity"]
        row.unit = step["unit"]
        db.add(row)
        db.add(
            InventoryTransaction(
                item_id=item.id,
                location_id=step["location_id"],
                quantity_change=Decimal("0") - step["quantity"],
                unit=step["unit"],
                transaction_type="remove",
                reason="plate_recipe",
                note=note,
            )
        )

    # One meal, but possibly several locations to take it from. The FIRST event
    # carries the calorie snapshot and is the meal in the day's log; the rest are
    # marked as continuations so the rollup deducts the stock without listing the
    # same dinner twice or counting its calories again.
    plated = steps or [{"location_id": None, "quantity": scale, "unit": "serving"}]
    event_id = None
    for index, step in enumerate(plated):
        primary = index == 0
        event = repository.append_event(
            db,
            event_type="consumed",
            item_id=item.id,
            # Recorded against the location the food actually came from, in the
            # unit it is stocked in, so the projector folds the balance and the
            # lot/container allocation like any other removal. None when there
            # was nothing to deduct — the projector skips the balance delta for a
            # location-less event, which is the original stock-less behaviour.
            location_id=step["location_id"],
            quantity=step["quantity"],
            unit=step["unit"],
            source="api",
            actor=profile_id,
            reason="plate_recipe",
            note=note,
            occurred_at=occurred_at,
            payload={
                "calories": None if not primary or macros["calories"] is None else float(macros["calories"]),
                "protein_g": None if not primary or macros["protein_g"] is None else float(macros["protein_g"]),
                "fat_g": None if not primary or macros["fat_g"] is None else float(macros["fat_g"]),
                "carbs_g": None if not primary or macros["carbs_g"] is None else float(macros["carbs_g"]),
                "profile_id": profile_id,
                "weight_grams": None,
                # Servings stay the DISPLAYED amount ("2 servings of the chili")
                # even when the event's own quantity is in the stock unit — same
                # split `plate` makes between what was eaten and what moved.
                "quantity": float(scale) if primary else 0,
                "unit": "serving",
                "meal_type": meal_type,
                "recipe_id": str(recipe_id),
                # Set on every event after the first: the same plate, continued
                # at another location. The rollup hides these so one dinner is
                # one entry (see `daily_calorie_rollup`).
                **({} if primary else {"continues_meal": str(event_id)}),
            },
        )
        if primary:
            event_id = event.event_id
    rebuild_projections(db)
    db.commit()
    deducted = sum((s["quantity"] for s in steps), Decimal("0")) if steps else None
    return (
        event_id,
        item,
        scale,
        macros,
        bool(nutrition["complete"]),
        deducted,
        steps[0]["unit"] if steps else None,
        steps[0]["location_id"] if steps else None,
    )


def _resolve_output_deduction(db: Session, recipe, item: Item, *, servings: Decimal) -> list[dict]:
    """Where eating ``servings`` of a recipe takes its stock from, first-expiring-first.

    A serving of the output is the recipe's own declared yield divided by its
    base yield — a recipe that makes 60 oz over 8 servings puts 7.5 oz on a
    plate — converted into whatever unit the output item is stocked in.

    Returns one entry per location drawn from, because a batch can be split
    across them: 100 g in the fridge and 500 g in the freezer must not cap a
    300 g dinner at 100 g, which would log the whole meal and leave 200 g of
    already-eaten pork on the books.

    Returns ``[]`` whenever the amount can't be established honestly: no output
    item, no declared output quantity, no stock anywhere, or no bridge to the
    stocked unit. Never raises — failing to size a serving must not block
    someone logging their dinner.
    """
    if recipe.output_item_id is None or item.id != recipe.output_item_id:
        return []
    if not recipe.output_quantity or not recipe.servings:
        return []

    per_serving = Decimal(recipe.output_quantity) / Decimal(recipe.servings)
    wanted_in_output_unit = per_serving * Decimal(servings)

    steps: list[dict] = []
    for row in list_item_stock_fefo(db, item.id):
        remaining = wanted_in_output_unit - sum(
            (s["in_output_unit"] for s in steps), Decimal("0")
        )
        if remaining <= 0:
            break
        # Each location's balance may be stored in its own unit, so the wanted
        # amount is converted per row rather than once up front.
        stock_unit = row["unit"]
        converted = convert_amount(
            db, item, remaining, from_unit=recipe.output_unit or "each", to_unit=stock_unit
        )
        if converted is None or converted <= 0:
            continue
        # Never take more than that location holds: eating the last of a batch
        # that was partly untracked is a real evening, and it must not drive a
        # balance negative. The calories logged are still the full portion eaten.
        take = min(converted, Decimal(row["quantity"]))
        if take <= 0:
            continue
        back = convert_amount(
            db, item, take, from_unit=stock_unit, to_unit=recipe.output_unit or "each"
        )
        steps.append(
            {
                "location_id": row["location_id"],
                "quantity": take,
                "unit": stock_unit,
                # How much of the requested portion this row satisfied, so the
                # loop can stop once the plate is covered.
                "in_output_unit": back if back is not None else remaining,
            }
        )
    return steps


def nutrition_summary(
    db: Session,
    *,
    start: date,
    end: date,
    profile_id: UUID | None = None,
    tz: str | None = None,
) -> dict:
    """Calories/macros per day across a date range, plus range totals.

    One person when ``profile_id`` is given, otherwise every active profile
    pooled. Days with nothing logged are included as zeros so a caller can chart
    the range without filling gaps itself.
    """
    if end < start:
        raise ValueError("`to` must not be before `from`")
    if (end - start).days > 366:
        raise ValueError("Range is limited to 366 days")

    profiles = (
        [p for p in [_get_profile(db, profile_id)] if p is not None]
        if profile_id is not None
        else list(
            db.execute(
                select(HouseholdProfile).where(HouseholdProfile.is_active.is_(True))
            ).scalars().all()
        )
    )
    if profile_id is not None and not profiles:
        raise LookupError("Profile not found")

    keys = ("calories", "protein_g", "fat_g", "carbs_g")
    days: list[dict] = []
    totals = {k: Decimal("0") for k in keys}
    complete = True

    day = start
    while day <= end:
        per_day = {k: Decimal("0") for k in keys}
        for profile in profiles:
            rollup = daily_calorie_rollup(db, profile_id=profile.id, on=day, tz=tz)
            per_day["calories"] += rollup["total_calories"]
            for k in ("protein_g", "fat_g", "carbs_g"):
                value = rollup[f"total_{k}"]
                if value is not None:
                    per_day[k] += value
            complete = complete and rollup["complete"]
        for k in keys:
            totals[k] += per_day[k]
        days.append({"date": day, **{k: per_day[k] for k in keys}})
        day += timedelta(days=1)

    span = len(days) or 1
    return {
        "from": start,
        "to": end,
        "profile_id": profile_id,
        "complete": complete,
        "days": days,
        "total": {k: totals[k] for k in keys},
        "daily_average": {k: (totals[k] / span).quantize(Decimal("0.01")) for k in keys},
    }


def _get_profile(db: Session, profile_id: UUID) -> HouseholdProfile | None:
    return db.execute(
        select(HouseholdProfile).where(HouseholdProfile.id == profile_id)
    ).scalar_one_or_none()


def _resolve_zone(tz: str | None) -> ZoneInfo:
    """The IANA zone events are bucketed into for "today" — defaults to UTC
    for tz-less/API-only callers. An unknown tz string falls back to UTC
    rather than erroring (a stale/garbled browser tz shouldn't 500 the page)."""
    if not tz:
        return ZoneInfo("UTC")
    try:
        return ZoneInfo(tz)
    except ZoneInfoNotFoundError:
        return ZoneInfo("UTC")


def today_in_zone(tz: str | None) -> date:
    """"Today" in ``tz`` — never the server's UTC day.

    The distinction is not cosmetic. Containers run UTC, so ``date.today()``
    rolls over at 20:00 EDT: ask "what did I eat today" at nine in the evening
    in New York and the UTC day is already tomorrow, so the query asks for a day
    that has barely begun and the entire evening's meals disappear. Any default
    for a calendar day has to be resolved in the caller's zone, not the
    server's.
    """
    return datetime.now(_resolve_zone(tz)).date()


def daily_calorie_rollup(
    db: Session, *, profile_id: UUID, on: date | None = None, tz: str | None = None
) -> dict:
    """Fold a profile's ``consumed`` events for ``on`` into a daily calorie total.

    Sums the payload calorie snapshot of every plate the profile ate that day and
    reports it against the profile's ``daily_calorie_target``. Events without a
    calorie snapshot (item had no ``calories_per_unit``) contribute an entry with
    ``calories: null`` and don't move the total.

    ``on`` is a calendar day with no zone of its own — ``tz`` (an IANA name,
    e.g. "America/New_York") says which zone to read it in. Without it, "today"
    is the server's UTC day, which silently rolls over hours before/after the
    household's actual midnight and makes an evening's meals vanish from (or
    bleed into) the wrong day — callers should always pass the browser's zone.
    """
    profile = _get_profile(db, profile_id)
    if profile is None:
        raise LookupError("Profile not found")
    zone = _resolve_zone(tz)
    if on is None:
        on = today_in_zone(tz)

    rows = db.execute(
        select(InventoryEvent, Item)
        .join(Item, InventoryEvent.item_id == Item.id)
        .where(
            InventoryEvent.event_type == "consumed",
            InventoryEvent.actor == str(profile_id),
        )
        .order_by(InventoryEvent.seq.asc())
    ).all()

    # Deleted meals (see delete_plate): the compensating entry's note carries
    # the original event's id, so both the mistake and its correction can be
    # hidden from the list below — their (positive, negative) payloads are
    # still summed into the totals, so the net contribution is zero either way.
    deleted_ids = {
        event.note[len(_MEAL_DELETE_MARKER_PREFIX):]
        for event, _item in rows
        if event.note and event.note.startswith(_MEAL_DELETE_MARKER_PREFIX)
    }

    entries: list[dict] = []
    total = Decimal("0")
    macro_totals = {"protein_g": Decimal("0"), "fat_g": Decimal("0"), "carbs_g": Decimal("0")}
    macro_seen = {"protein_g": False, "fat_g": False, "carbs_g": False}

    def _dec(value: object) -> Decimal | None:
        return None if value is None else Decimal(str(value))

    for event, item in rows:
        if event.occurred_at is None:
            continue
        occurred_at = event.occurred_at
        if occurred_at.tzinfo is None:
            occurred_at = occurred_at.replace(tzinfo=_timezone.utc)
        if occurred_at.astimezone(zone).date() != on:
            continue
        payload = event.payload or {}
        calories = _dec(payload.get("calories"))
        if calories is not None:
            total += calories
        macros = {k: _dec(payload.get(k)) for k in macro_totals}
        for k, value in macros.items():
            if value is not None:
                macro_totals[k] += value
                macro_seen[k] = True

        is_deletion_marker = bool(event.note and event.note.startswith(_MEAL_DELETE_MARKER_PREFIX))
        if is_deletion_marker or str(event.event_id) in deleted_ids:
            continue
        # A recipe meal drawn from more than one location writes one event per
        # location so each balance is decremented where the food actually was.
        # Only the first is the meal; the continuations carry no calories (so
        # the totals above are already right) and are hidden here so one dinner
        # doesn't appear in the log twice (ADR 0025 §5).
        if payload.get("continues_meal"):
            continue

        # Prefer the eaten amount snapshotted in the payload — the event's own
        # quantity/unit is in the stock unit (e.g. a fraction of a container),
        # not what was actually plated. Older events predating this snapshot
        # fall back to the event fields (their quantity/unit was the eaten
        # amount verbatim, since plate() didn't convert units yet).
        eaten_quantity = _dec(payload.get("quantity"))
        eaten_unit = payload.get("unit")
        entries.append(
            {
                "event_id": event.event_id,
                "item_name": item.name,
                "quantity": eaten_quantity if eaten_quantity is not None else Decimal(event.quantity),
                "unit": eaten_unit if eaten_unit is not None else event.unit,
                "weight_grams": _dec(payload.get("weight_grams")),
                "calories": calories,
                "protein_g": macros["protein_g"],
                "fat_g": macros["fat_g"],
                "carbs_g": macros["carbs_g"],
                "meal_type": payload.get("meal_type"),
                "occurred_at": event.occurred_at,
            }
        )

    return {
        "profile_id": profile.id,
        "date": on,
        # False when ANY entry had no usable nutrition — so a partial total is
        # never read as a complete one (the entries themselves carry null).
        "complete": all(e["calories"] is not None for e in entries),
        "total_calories": total,
        "total_protein_g": macro_totals["protein_g"] if macro_seen["protein_g"] else None,
        "total_fat_g": macro_totals["fat_g"] if macro_seen["fat_g"] else None,
        "total_carbs_g": macro_totals["carbs_g"] if macro_seen["carbs_g"] else None,
        "target": profile.daily_calorie_target,
        "entries": entries,
    }


def household_calorie_summary(db: Session, *, on: date | None = None, tz: str | None = None) -> list[dict]:
    """Per-profile calorie totals for ``on`` (feeds the dashboard). Includes every
    active profile, even those who ate nothing that day (total 0). See
    ``daily_calorie_rollup`` for what ``tz`` does and why it matters."""
    if on is None:
        on = today_in_zone(tz)
    profiles = db.execute(
        select(HouseholdProfile)
        .where(HouseholdProfile.is_active.is_(True))
        .order_by(HouseholdProfile.display_name.asc())
    ).scalars().all()

    summary: list[dict] = []
    for profile in profiles:
        rollup = daily_calorie_rollup(db, profile_id=profile.id, on=on, tz=tz)
        summary.append(
            {
                "profile_id": profile.id,
                "display_name": profile.display_name,
                "total_calories": rollup["total_calories"],
                "total_protein_g": rollup["total_protein_g"],
                "total_fat_g": rollup["total_fat_g"],
                "total_carbs_g": rollup["total_carbs_g"],
                "target": profile.daily_calorie_target,
            }
        )
    return summary
