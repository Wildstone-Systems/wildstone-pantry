"""Ad hoc cooking — cook first, recipe after (ADR 0024).

The session IS a draft ``Recipe`` row (``is_draft=True``), created up front and
appended to live: ingredient uses become ``recipe_ingredients`` rows (merged
per item, exactly like the import path), steps append to ``instructions`` as
sequentially numbered lines — the canonical shape ``split_instructions``
prefers, so a recipe born ad hoc walks through cook-along like any other.

Finish inverts the recipe-cook path's authority ordering (§3): the food is
already in the pan, so deductions run to zero and shortfalls are *reported*,
never refused.
"""

from __future__ import annotations

import logging
from decimal import ROUND_HALF_UP, Decimal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Recipe, RecipeCookEvent, RecipeIngredient
from modules.catalog.services.units import convert_quantity
from modules.inventory.services.inventory import get_location, remove_inventory
from modules.planning.services.recipes import (
    _plan_ingredient_deductions_report,
    _primary_source_location,
    get_recipe,
    resolve_ingredient_specs,
)

logger = logging.getLogger(__name__)

# A draft that was never named. Finish refuses to promote a recipe still
# carrying this — "Untitled cook" in the recipe list helps nobody.
DRAFT_DEFAULT_NAME = "Untitled cook"


def _require_draft(db: Session, recipe_id: UUID) -> Recipe:
    recipe = get_recipe(db, recipe_id)
    if not recipe:
        raise LookupError("Recipe not found")
    if not recipe.is_draft:
        raise ValueError("Not an ad hoc draft — edit the recipe through the editor instead")
    return recipe


def _merge_ingredient_use(db: Session, recipe_id: UUID, entry: dict) -> RecipeIngredient:
    """Fold one ingredient use into the draft's ingredient list.

    Garlic added at step 2 and again at step 6 is ONE recipe line — same rule
    as ``merge_duplicate_lines`` on import: merge when the units convert (the
    sum is exact arithmetic), keep a separate row when they don't (combining
    "200 g" with "1 cup" would need a density guess, which ADR 0017 forbids).

    Updates rows in place rather than rewriting the list, so ingredient row
    ids stay stable across appends.
    """
    rows = (
        db.execute(select(RecipeIngredient).where(RecipeIngredient.recipe_id == recipe_id))
        .scalars()
        .all()
    )
    quantity = Decimal(str(entry["quantity_required"]))
    for row in rows:
        if row.item_id != entry["item_id"]:
            continue
        converted = convert_quantity(quantity, entry["unit"], row.unit)
        if converted is None:
            continue
        row.quantity_required = Decimal(str(row.quantity_required)) + converted
        # Optional only if every use said optional — one measured use makes it real.
        row.optional = bool(row.optional) and bool(entry.get("optional", False))
        return row
    row = RecipeIngredient(
        recipe_id=recipe_id,
        item_id=entry["item_id"],
        quantity_required=quantity,
        unit=entry["unit"],
        optional=bool(entry.get("optional", False)),
    )
    db.add(row)
    return row


def _append_step(recipe: Recipe, text: str) -> None:
    """Append one step as the next ``N. text`` line.

    Sequential numbering from 1, one step per line — deliberately the exact
    shape the cook-along splitter prefers (ADR 0022 §3), so the walkthrough
    works on the saved recipe with no translation. Inner newlines collapse to
    spaces because a line break IS the step delimiter.
    """
    step = " ".join(text.strip().split())
    existing = (recipe.instructions or "").strip()
    number = len([line for line in existing.splitlines() if line.strip()]) + 1 if existing else 1
    line = f"{number}. {step}"
    recipe.instructions = f"{existing}\n{line}" if existing else line


def append_ad_hoc_entry(
    db: Session,
    recipe_id: UUID,
    *,
    ingredients: list[dict] | None = None,
    step_text: str | None = None,
) -> list[dict]:
    """Record one cooking action against a draft: ingredient use(s), a step,
    or both at once. "Diced the onion" is one event producing both halves, and
    "whisk the flour, sugar and salt" is one event using three ingredients
    (ADR 0024 §2) — the batch resolves together, so duplicates *within* it
    merge before they ever touch the draft. Commits immediately: the draft is
    the durable record, so closing the tab mid-cook loses nothing. Returns any
    flagged units."""
    recipe = _require_draft(db, recipe_id)
    ingredients = ingredients or []
    if not ingredients and not (step_text or "").strip():
        raise ValueError("An entry needs an ingredient, a step, or both")

    flagged: list[dict] = []
    if ingredients:
        # The same resolution as import (ADR 0015/0021): name → catalog item,
        # unit folding, flagged units. Ad hoc is the highest-velocity
        # item-creation path in the app, so it MUST go through the name
        # hygiene here rather than around it.
        inputs, _created, flagged = resolve_ingredient_specs(db, ingredients, create_missing=True)
        for entry in inputs:
            _merge_ingredient_use(db, recipe_id, entry)
    if step_text and step_text.strip():
        _append_step(recipe, step_text)
    db.commit()
    return flagged


def start_ad_hoc_cook(
    db: Session, *, name: str | None = None, entries: list[dict] | None = None
) -> tuple[Recipe, list[dict]]:
    """Open an ad hoc cook: a draft recipe row, optionally seeded with first
    entries (the "use these up" entry point passes expiring items here)."""
    recipe = Recipe(
        name=(name or "").strip()[:200] or DRAFT_DEFAULT_NAME,
        servings=1,
        is_draft=True,
        source="ad_hoc",
    )
    db.add(recipe)
    db.commit()
    db.refresh(recipe)

    flagged: list[dict] = []
    for entry in entries or []:
        flagged.extend(
            append_ad_hoc_entry(
                db,
                recipe.id,
                ingredients=entry.get("ingredients"),
                step_text=entry.get("step_text"),
            )
        )
    return recipe, flagged


def list_ad_hoc_drafts(db: Session) -> list[Recipe]:
    """Open drafts, newest first — the resume surface. The only place drafts
    are listed; ``list_recipes`` excludes them by design (ADR 0024 §1)."""
    return list(
        db.execute(
            select(Recipe).where(Recipe.is_draft.is_(True)).order_by(Recipe.created_at.desc())
        )
        .scalars()
        .all()
    )


def finish_ad_hoc_cook(
    db: Session,
    recipe_id: UUID,
    *,
    servings_made: Decimal,
    save: bool,
    name: str | None = None,
    location_id: UUID | None = None,
    leftover_servings: int = 0,
    note: str | None = None,
    actor: str | None = None,
) -> dict:
    """Close out an ad hoc cook: deduct what was recorded, then promote the
    draft to a real recipe (``save``) or delete it keeping the deductions.

    Deducts to zero and NEVER refuses on shortfall (ADR 0024 §3): this records
    what already happened, so the ledger doesn't get to veto reality. Gaps come
    back in the result — ``shortfalls`` for lines stock couldn't fully cover,
    ``unconvertible`` for lines with no unit bridge (those deduct nothing
    rather than guess a conversion, per ADR 0017).
    """
    recipe = _require_draft(db, recipe_id)
    if location_id is not None and not get_location(db, location_id):
        raise LookupError("Location not found")

    final_name = (name or "").strip()[:200]
    if save and not final_name:
        if recipe.name and recipe.name != DRAFT_DEFAULT_NAME:
            final_name = recipe.name
        else:
            raise ValueError("Name the recipe to save it")

    ingredients = (
        db.execute(select(RecipeIngredient).where(RecipeIngredient.recipe_id == recipe_id))
        .scalars()
        .all()
    )

    # Amounts were recorded absolute, as they went into the pan — scale is
    # identically 1. (`servings_made` is the yield, not a scaling input; the
    # per-serving ambiguity ADR 0022 §9 fights cannot arise here.)
    plan, shortfalls, unconvertible = _plan_ingredient_deductions_report(
        db,
        ingredients=list(ingredients),
        location_id=location_id,
        scale=Decimal(1),
    )

    tx_ids: list[UUID] = []
    deducted: list[dict] = []
    for step in plan:
        tx, _row, item = remove_inventory(
            db,
            item_id=step["item_id"],
            location_id=step["location_id"],
            quantity=step["quantity"],
            unit=step["unit"],
            reason="recipe_cook",
            note=f"recipe:{recipe_id}",
            actor=actor,
        )
        tx_ids.append(tx.id)
        deducted.append(
            {
                "item_id": step["item_id"],
                "item_name": item.name,
                "quantity": float(step["quantity"]),
                "unit": step["unit"],
            }
        )

    # Attribute the cook to the pinned location, else the biggest contributor.
    # A cook where NOTHING could be deducted (empty ledger) has no location to
    # stamp a cook event with — the recipe still saves; the event is skipped
    # and the response says so rather than inventing a location.
    event_location = location_id
    if event_location is None and plan:
        event_location = _primary_source_location(db, plan)

    cook_event_id: UUID | None = None
    if save:
        recipe.is_draft = False
        recipe.name = final_name
        # The base yield is what was ACTUALLY made — known, not guessed
        # (ADR 0024 §2). Column is an int; the cook event keeps the exact figure.
        recipe.servings = max(1, int(Decimal(servings_made).to_integral_value(rounding=ROUND_HALF_UP)))
        if event_location is not None:
            event = RecipeCookEvent(
                recipe_id=recipe.id,
                location_id=event_location,
                servings=1,
                leftover_servings=leftover_servings,
                note=note,
                servings_made=Decimal(servings_made),
            )
            db.add(event)
            db.commit()
            db.refresh(event)
            cook_event_id = event.id
        else:
            db.commit()
    else:
        # The deductions' transactions are independent ledger events and stand;
        # the draft row (and its ingredients, via the ORM cascade) goes. No cook
        # event either — its reason to exist is recipe history, and there is
        # deliberately no recipe (ADR 0024 §4).
        db.delete(recipe)
        db.commit()

    return {
        "recipe_id": recipe_id,
        "saved": save,
        "cook_logged": cook_event_id is not None,
        "cook_event_id": cook_event_id,
        "location_id": event_location,
        "deducted_transaction_ids": tx_ids,
        "deducted": deducted,
        "shortfalls": [
            {"item_name": s["item_name"], "short": float(s["short"]), "unit": s["unit"]}
            for s in shortfalls
        ],
        "unconvertible": [
            {"item_name": u["item_name"], "reason": u["message"]} for u in unconvertible
        ],
    }
