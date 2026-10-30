from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from types import SimpleNamespace
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.models import (
    Inventory,
    Item,
    Recipe,
    RecipeCookEvent,
    RecipeIngredient,
)
from modules.catalog.schemas.item import ItemCreate
from modules.catalog.services.barcodes import find_item_by_name
from modules.catalog.services.canonical import canonical_ingredient, subset_suggestion
from modules.catalog.services.ingredient_reference import (
    build_reference_index,
    macros_for_amount,
    resolve_hints,
)
from modules.catalog.services.items import create_item, get_item
from modules.catalog.services.shelf_life import LEFTOVER_SHELF_LIFE_DAYS
from modules.catalog.services.units import (
    convert_quantity,
    default_base_unit,
    suggest_unit,
    to_recipe_unit,
    unit_dimension,
)
from modules.inventory.services.inventory import (
    base_unit_of,
    convert_amount,
    get_location,
    list_items_stock_fefo,
    list_packages_fefo,
    produce_inventory,
    remove_inventory,
)
from modules.planning import repository as planning_repository
from modules.planning.services.substitutions import (
    basis_matches,
    canonical_subs_by_from,
    cook_basis,
    upsert_substitution,
)

logger = logging.getLogger(__name__)

_OUTCOME_RANK = {"direct": 0, "acceptable": 1, "emergency": 2}

_DIMENSION_BASE = {"mass": "g", "volume": "ml", "count": "each"}


def _display_amount(value: Decimal) -> Decimal:
    """Round a scaled amount to 3dp and drop trailing zeros, for display only.

    Scaling divides by the recipe's base yield, which for thirds yields
    Decimal's full 28-digit expansion. 3dp matches the Numeric(12,3) precision
    quantities are stored at everywhere else.
    """
    quantized = value.quantize(Decimal("0.001"), rounding=ROUND_HALF_UP)
    normalized = quantized.normalize()
    # normalize() renders small integers in exponent form (2 -> 2E+0); expand it.
    return normalized if normalized.as_tuple().exponent <= 0 else normalized.quantize(Decimal(1))


def _comparison_unit(recipe_unit: str) -> str:
    """The canonical unit a recipe amount and on-hand are compared/deducted in —
    the dimension of the RECIPE's own unit (cup→ml, oz→g, each→each), NOT the
    item's declared base. Keying off the recipe unit means an under-specified item
    (stocked in a lenient unit) or a pooled sibling in a different base still
    compares correctly (ADR 0017 §1, §6). An unknown recipe unit falls to 'each',
    where the convert will fail → the caller shows can't-convert."""
    return _DIMENSION_BASE.get(unit_dimension(recipe_unit), "each")


def _pooled_have(
    db: Session,
    *,
    target_unit: str,
    pool_ids: list[UUID],
    qty_by_item: dict[UUID, Decimal],
    unit_by_item: dict[UUID, str],
    items_by_id: dict[UUID, Item],
    ref_index: dict[str, dict],
) -> tuple[Decimal, Decimal, bool]:
    """Pooled on-hand expressed in ``target_unit`` (ADR 0017 §6). Returns
    (have_in_target, raw_on_hand, all_bridged): each stocked member is converted
    from its own stored unit into the target using its own hints; a member with no
    bridge is excluded and flips all_bridged False (so the caller can say
    'can't tell' rather than silently undercount)."""
    have = Decimal("0")
    raw = Decimal("0")
    all_bridged = True
    for mid in pool_ids:
        q = qty_by_item.get(mid, Decimal("0"))
        if q <= 0:
            continue
        raw += q
        member = items_by_id.get(mid)
        conv = (
            convert_amount(
                db, member, q, from_unit=unit_by_item.get(mid, base_unit_of(member)),
                to_unit=target_unit, hints=resolve_hints(db, member, index=ref_index),
            )
            if member is not None
            else None
        )
        if conv is None:
            all_bridged = False
            continue
        have += conv
    return have, raw, all_bridged


def _pool_for_canon(canon: str | None, item_id: UUID, qty_by_item, canon_by_item) -> list[UUID]:
    """Stocked item ids that satisfy an ingredient: everything sharing its canonical
    key, or just the item itself when it has no key."""
    if canon:
        return [mid for mid in qty_by_item if canon_by_item.get(mid) == canon]
    return [item_id]


def _sub_available(
    db, *, sub, recipe_needed, recipe_unit,
    canon_by_item, qty_by_item, unit_by_item, items_by_id, ref_index,
):
    """Is one substitution satisfiable, and how much is on hand (ADR 0017 §7)?

    Measures the substituted requirement (recipe_needed × ratio) in the SUBSTITUTE's
    own ``sub.unit`` first — the exact basis cook deducts in (the swap uses
    ``unit=sub.unit``) — so a cross-dimension mapping like '1 each → 60 g' is checked
    in grams. Falls back to the recipe's own unit when ``sub.unit`` doesn't resolve
    (API-created subs default to 'each', which is meaningless for e.g. oil). Returns
    (available: bool, have_in_the_unit_used)."""
    if not basis_matches(sub, recipe_unit):
        return False, Decimal("0")
    ratio = Decimal(sub.ratio)
    scaled = Decimal(recipe_needed) * ratio
    pool = [mid for mid in qty_by_item if canon_by_item.get(mid) == sub.to_canonical]
    for basis_unit in (sub.unit, recipe_unit):
        if not basis_unit:
            continue
        target = _comparison_unit(basis_unit)
        need = convert_quantity(scaled, basis_unit, target)  # pure, same dimension
        if need is None:
            continue
        have, _raw, _bridged = _pooled_have(
            db, target_unit=target, pool_ids=pool, qty_by_item=qty_by_item,
            unit_by_item=unit_by_item, items_by_id=items_by_id, ref_index=ref_index,
        )
        if have >= need:
            return True, have
    return False, Decimal("0")


def _best_sub(
    db, *, canon, recipe_needed, recipe_unit, subs_by_from, approved_only,
    canon_by_item, qty_by_item, unit_by_item, items_by_id, ref_index,
):
    """The best in-stock substitution (ranked by outcome), or None. ``approved_only``
    picks approved subs (counted as available) vs unapproved seeds (suggestions).
    Returns (sub, have) for the first that's satisfiable."""
    if not canon:
        return None
    candidates = sorted(
        (
            s for s in subs_by_from.get(canon, [])
            if s.to_canonical and (s.approved if approved_only else not s.approved)
        ),
        key=lambda s: _OUTCOME_RANK.get(s.outcome, 99),
    )
    for sub in candidates:
        ok, have = _sub_available(
            db, sub=sub, recipe_needed=recipe_needed, recipe_unit=recipe_unit,
            canon_by_item=canon_by_item, qty_by_item=qty_by_item, unit_by_item=unit_by_item,
            items_by_id=items_by_id, ref_index=ref_index,
        )
        if ok:
            return sub, have
    return None


def _coerce_output_quantity(value: object | None) -> Decimal | None:
    return None if value is None else Decimal(str(value))


def align_output_item_base_unit(db: Session, item_id: UUID | None, output_unit: str | None) -> None:
    """Configure a recipe's output item from the output unit the recipe declares
    (ADR 0025 §2).

    An item created without a size defaults to counted/'each' (ADR 0016 §1). A
    recipe that yields "60 oz of Pulled Pork" then can't record its own output:
    oz is mass, the item's base is a count, and the cross-dimension bridge needs a
    grams-per-each nobody has. The declaration itself is the missing information —
    the user has said, explicitly, that this thing is measured by weight — so the
    item takes the dimension of the unit it is yielded in.

    Deliberately narrow. Only an item that is **unconfigured** (still carrying the
    base unit its size implies, and no package size) and **unstocked** (no
    balance, no ledger history) is touched: re-expressing a balance recorded in
    one unit as another is exactly the silent reinterpretation ADR 0016 made a
    one-time, explicit migration.
    """
    if item_id is None or not output_unit:
        return
    base = _DIMENSION_BASE.get(unit_dimension(output_unit))
    if base is None or base == "each":
        return  # a counted yield ("1 loaf") is what the default already gives

    item = db.get(Item, item_id)
    if item is None or item.size_quantity is not None:
        return
    # "Unconfigured" means nobody has *chosen* anything: the item still carries
    # whatever `create_item` derived for it. The column is never null for an item
    # created through the API (catalog/services/items.py materialises the default
    # on write), so a null check alone would never fire — the default is what has
    # to be compared against.
    if (item.base_unit or "") != default_base_unit(item.stock_mode, item.size_unit):
        return

    from modules.inventory.models import InventoryEvent

    stocked = db.execute(
        select(func.count()).select_from(Inventory).where(Inventory.item_id == item_id)
    ).scalar_one()
    if stocked:
        return
    historied = db.execute(
        select(func.count()).select_from(InventoryEvent).where(InventoryEvent.item_id == item_id)
    ).scalar_one()
    if historied:
        return

    item.stock_mode = "measured"
    item.base_unit = base
    db.add(item)


def create_recipe(
    db: Session,
    *,
    name: str,
    instructions: str | None,
    servings: int = 1,
    output_item_id: UUID | None = None,
    output_quantity: Decimal | None = None,
    output_unit: str | None = None,
    output_mode: str | None = None,
    output_yield_ratio: Decimal | None = None,
    external_source: str | None = None,
    external_id: str | None = None,
    external_url: str | None = None,
    image_url: str | None = None,
    source: str | None = None,
    source_filename: str | None = None,
    source_text: str | None = None,
) -> Recipe:
    recipe = Recipe(
        name=name.strip(),
        instructions=instructions,
        servings=servings,
        output_item_id=output_item_id,
        output_quantity=_coerce_output_quantity(output_quantity),
        output_unit=output_unit,
        output_mode=output_mode or "fixed",
        output_yield_ratio=_coerce_output_quantity(output_yield_ratio),
        external_source=external_source,
        external_id=external_id,
        external_url=external_url,
        image_url=image_url,
        source=source, source_filename=source_filename, source_text=source_text,
    )
    db.add(recipe)
    align_output_item_base_unit(db, output_item_id, output_unit)
    db.commit()
    db.refresh(recipe)
    return recipe


def find_recipe_by_external_identity(
    db: Session, external_source: str | None, external_id: str | None
) -> Recipe | None:
    """The recipe previously imported under this upstream identity, if any.

    Both halves are required — a source with no id (or vice versa) identifies
    nothing, and matching on a half would collapse every recipe from a feed onto
    one row.
    """
    if not external_source or not external_id:
        return None
    return db.execute(
        select(Recipe).where(
            Recipe.external_source == external_source,
            Recipe.external_id == external_id,
        )
    ).scalar_one_or_none()


def list_recipes(db: Session) -> list[Recipe]:
    # Ad hoc drafts (ADR 0024 §1) are half-captured cooks, not recipes yet —
    # they must not appear in the list or anything built on it (suggestions
    # score `list_recipes`' output). They surface only via the ad hoc endpoints.
    return list(
        db.execute(
            select(Recipe).where(Recipe.is_draft.is_(False)).order_by(Recipe.created_at.desc())
        )
        .scalars()
        .all()
    )


def list_recipes_with_counts(db: Session) -> list[dict]:
    """Recipes plus how many ingredients each has, for the list view.

    A recipe with no ingredients can never be cooked or suggested, so the count
    is the one number that tells you whether a recipe is actually usable.
    """

    counts = dict(
        db.execute(
            select(RecipeIngredient.recipe_id, func.count(RecipeIngredient.id)).group_by(
                RecipeIngredient.recipe_id
            )
        ).all()
    )

    return [
        {
            "id": recipe.id,
            "name": recipe.name,
            "instructions": recipe.instructions,
            "servings": recipe.servings,
            "output_item_id": recipe.output_item_id,
            "output_quantity": recipe.output_quantity,
            "output_unit": recipe.output_unit,
            "output_mode": recipe.output_mode,
            "output_yield_ratio": recipe.output_yield_ratio,
            "created_at": recipe.created_at,
            "image_url": recipe.image_url,
            "external_source": recipe.external_source,
            "external_id": recipe.external_id,
            "external_url": recipe.external_url,
            "ingredient_count": int(counts.get(recipe.id, 0)),
        }
        for recipe in list_recipes(db)
    ]


class RecipeDeleteBusyError(ValueError):
    """A cook/confirmation must finish before a recipe can be deleted."""


def delete_recipe(db: Session, recipe_id: UUID) -> None:
    recipe = planning_repository.lock_recipe_for_update(db, recipe_id)
    if not recipe:
        raise LookupError("Recipe not found")

    try:
        sessions = planning_repository.lock_recipe_dependents_nowait(db, recipe_id)
    except DBAPIError as exc:
        # PostgreSQL lock_not_available: rolling back releases the parent so
        # the in-flight confirmation/terminal consume can complete normally.
        if getattr(exc.orig, "sqlstate", getattr(exc.orig, "pgcode", None)) != "55P03":
            raise
        db.rollback()
        raise RecipeDeleteBusyError("Recipe is busy with a cook or confirmation; try deleting it again afterward") from exc
    if any(session.status == "finishing" for session in sessions):
        db.rollback()
        raise RecipeDeleteBusyError("A cook is finishing; check its result before deleting this recipe")

    # Ingredients and cook events cascade at the FK level.
    db.delete(recipe)
    db.commit()


def get_recipe(db: Session, recipe_id: UUID) -> Recipe | None:
    return db.execute(select(Recipe).where(Recipe.id == recipe_id)).scalar_one_or_none()


def update_recipe(
    db: Session,
    recipe_id: UUID,
    *,
    name: str,
    instructions: str | None,
    servings: int = 1,
    output_item_id: UUID | None = None,
    output_quantity: Decimal | None = None,
    output_unit: str | None = None,
    output_mode: str | None = None,
    output_yield_ratio: Decimal | None = None,
    external_source: str | None = None,
    external_id: str | None = None,
    external_url: str | None = None,
    image_url: str | None = None,
    source: str | None = None,
    source_filename: str | None = None,
    source_text: str | None = None,
) -> Recipe:
    recipe = db.execute(select(Recipe).where(Recipe.id == recipe_id)).scalar_one_or_none()
    if not recipe:
        raise LookupError("Recipe not found")
    recipe.name = name.strip()
    recipe.instructions = instructions
    recipe.servings = servings
    recipe.output_item_id = output_item_id
    recipe.output_quantity = _coerce_output_quantity(output_quantity)
    recipe.output_unit = output_unit
    # Same rule as the external identity and the picture below: only ever SET,
    # never clear. The recipe editor's payload predates these fields, so a plain
    # rename would otherwise demote a yield_ratio recipe to a fixed one and
    # discard its ratio — an unrelated edit silently changing what a cook yields.
    if output_mode is not None:
        recipe.output_mode = output_mode
    if output_yield_ratio is not None:
        recipe.output_yield_ratio = _coerce_output_quantity(output_yield_ratio)
    align_output_item_base_unit(db, output_item_id, output_unit)
    # Only ever set external identity, never clear it: a metadata-only PUT that
    # omits the block must not orphan a recipe from the feed that created it.
    if external_source and external_id:
        recipe.external_source = external_source
        recipe.external_id = external_id
    if external_url:
        recipe.external_url = external_url
    # Same rule as the external identity: only ever set, never clear. A
    # metadata-only PUT that omits the field must not wipe the recipe's picture.
    if image_url:
        recipe.image_url = image_url
    if source is not None:
        recipe.source = source
    if source_filename is not None:
        recipe.source_filename = source_filename
    if source_text is not None:
        recipe.source_text = source_text
    db.commit()
    db.refresh(recipe)
    return recipe


def merge_duplicate_lines(inputs: list[dict], *, track_indexes: bool = False) -> list[dict]:
    """Collapse repeated ingredient lines for the same item, summing quantities.

    Real-world recipes list the same ingredient twice ("1 tbsp oil" for the pan,
    "2 tbsp oil" for the sauce), and an importer that POSTs both leaves the recipe
    with two rows for one item — availability then scores it twice and cook deducts
    against whichever row it sees first.

    Two lines merge only when their units share a dimension, so the sum is exact
    arithmetic (2 tbsp + 1 cup → 17 tbsp). Cross-dimension lines ("200 g" + "1 cup")
    would need the item's density to combine, and guessing there is precisely what
    ADR 0017 forbids — those stay as separate rows. Merged lines are optional only
    if *every* line merged into them was optional.
    """
    merged: list[dict] = []
    for entry in inputs:
        for existing in merged:
            if (existing.get("quantity_mode") or "amount") != (entry.get("quantity_mode") or "amount"):
                continue
            if existing["item_id"] != entry["item_id"]:
                continue
            # A package amount is a count; its unit label is ignored at cook
            # time and must not turn one kilogram-labelled package into 1000.
            converted = (
                Decimal(str(entry["quantity_required"]))
                if entry.get("quantity_mode") == "package"
                else convert_quantity(
                    Decimal(str(entry["quantity_required"])),
                    entry["unit"],
                    existing["unit"],
                )
            )
            if converted is None:
                continue
            existing["quantity_required"] = float(
                Decimal(str(existing["quantity_required"])) + converted
            )
            existing["optional"] = bool(existing.get("optional")) and bool(entry.get("optional"))
            # Unlike `optional` (merged only if EVERY half was), the basis flag
            # is a union: if either line was the yield basis, the line that
            # replaces them both still is.
            existing["yield_basis"] = existing.get("yield_basis") or entry.get("yield_basis")
            if track_indexes:
                existing["_indexes"] = existing.get("_indexes", []) + entry.get("_indexes", [])
            break
        else:
            merged.append(dict(entry))
    return merged


def resolve_ingredient_specs(
    db: Session, specs: list[dict], *, create_missing: bool,
    expected_resolution: dict[int, UUID | None] | None = None,
) -> tuple[list[dict], list[UUID], list[dict]]:
    """Turn ``{item_id|name, quantity, unit}`` specs into id-based ingredient rows.

    Names are resolved against the catalog (case-insensitive); an unmatched name
    is created as a new item when ``create_missing`` is set, otherwise it's an
    error. Duplicate lines for one item are merged (see ``merge_duplicate_lines``).

    Returns the id-based inputs, the ids of any items created, and a ``flagged``
    list naming every unit that could not be resolved to the vocabulary — the
    import path keeps such a line (ADR 0017 §3's flag path) but the caller has to
    be *told*, or the "can't tell" state is invisible until someone tries to cook.

    Reviewed imports may bind each source index to the catalog identity seen
    during confirmation. Check at the actual lookup boundary: a catalog writer
    need not participate in import advisory locks. Missing identities may reuse
    only items created by this same batch. Ordinary callers omit the guard.
    """
    preview = preview_ingredient_specs(db, specs, create_missing=create_missing)
    if preview["errors"]:
        raise LookupError("; ".join(preview["errors"]))
    inputs: list[dict] = []
    created_ids: list[UUID] = []
    created_items: list[Item] = []
    flagged: list[dict] = []

    for index, spec in enumerate(specs):
        item_id = spec.get("item_id")
        name = (spec.get("name") or "").strip()
        if item_id is None:
            if not name:
                continue
            item = find_item_by_name(db, name)
            if expected_resolution is not None:
                expected = expected_resolution[index]
                actual = item.id if item else None
                if (expected is not None and actual != expected) or (
                    expected is None and actual is not None and actual not in created_ids
                ):
                    raise ValueError("Ingredient catalog identity changed after review; preview and confirm again")
            if item is None:
                if not create_missing:
                    raise LookupError(f"No catalog item named '{name}' (set create_missing_items to add it)")
                item = create_item(db, ItemCreate(name=name))
                created_ids.append(item.id)
                created_items.append(item)
            item_id = item.id

        raw_unit = spec.get("unit") or "each"
        # Fold known aliases to the vocabulary; keep an unknown token so it
        # surfaces as "can't tell" and gets flagged for remap (ADR 0017 §3).
        canonical = to_recipe_unit(raw_unit)
        if canonical is None:
            flagged.append(
                {
                    "index": index,
                    "item_name": name or None,
                    "unit": raw_unit,
                    "suggestion": suggest_unit(raw_unit),
                }
            )
        inputs.append(
            {
                "item_id": item_id,
                "quantity_required": spec.get("quantity", 1.0),
                "unit": canonical or raw_unit,
                "optional": bool(spec.get("optional", False)),
                "yield_basis": spec.get("yield_basis"),
                "quantity_mode": spec.get("quantity_mode"),
            }
        )

    # A brand-new ingredient arrives as nothing but a name. Look up what we can
    # while we're here — the name is already the plain food, which is the ideal
    # query — so it doesn't land in the review queue by default. Best-effort and
    # capped; see modules.catalog.services.enrichment.
    if created_items:
        try:
            from modules.catalog.services.enrichment import enrich_new_items

            enrich_new_items(db, created_items)
        except Exception:  # noqa: BLE001
            # Enrichment is a bonus, never a precondition. Individual lookups
            # already swallow their own failures; this guards the batch call
            # itself so an unexpected error can never cost someone their import.
            logger.exception("ingredient enrichment failed; import continues")

    merged = merge_duplicate_lines(inputs)
    for row in merged:
        row["quantity_required"] = _display_amount(Decimal(str(row["quantity_required"])))
    return merged, created_ids, flagged


def preview_ingredient_specs(db: Session, specs: list[dict], *, create_missing: bool) -> dict:
    """Resolve ingredient specs **without writing anything** — the /validate path.

    Mirrors ``resolve_ingredient_specs`` decision for decision (item lookup, unit
    fold-or-flag, duplicate merging) but creates no items and no rows, so an
    importer can see what a payload would do before committing to it. Anything that
    would be a hard error at import (an unknown name with ``create_missing`` off) is
    reported in ``errors`` rather than raised, so one bad line does not hide the
    other twenty.
    """
    lines: list[dict] = []
    errors: list[str] = []
    would_create_items: list[str] = []
    pending: list[tuple[str, str | None]] = []

    for index, spec in enumerate(specs):
        item_id = spec.get("item_id")
        name = (spec.get("name") or "").strip()
        resolution = "by_id"
        resolved_name = name or None

        if item_id is None:
            if not name:
                errors.append(f"line {index}: needs either item_id or name")
                continue
            item = find_item_by_name(db, name)
            if item is not None:
                item_id, resolution, resolved_name = item.id, "matched", item.name
            elif create_missing:
                resolution = "would_create"
                key = canonical_ingredient(name)
                staged = next((n for n, k in pending if n.lower() == name.lower() or (key and key == k)), None)
                if staged is None:
                    pending.append((name, key))
                    would_create_items.append(name)
                else:
                    resolved_name = staged
            else:
                resolution = "unresolved"
                errors.append(f"line {index}: no catalog item named '{name}' (set create_missing_items to add it)")
        else:
            item = get_item(db, item_id)
            if item is None:
                resolution = "unresolved"
                errors.append(f"line {index}: item_id {item_id} not found")
            else:
                resolved_name = item.name

        raw_unit = spec.get("unit") or "each"
        canonical = to_recipe_unit(raw_unit)
        lines.append(
            {
                "index": index,
                "raw_line": spec.get("raw_line"),
                "quantity_mode": spec.get("quantity_mode") or "amount",
                "yield_basis": bool(spec.get("yield_basis")),
                "optional": bool(spec.get("optional", False)),
                "name": resolved_name,
                "item_id": item_id,
                "resolution": resolution,
                "quantity": float(spec.get("quantity", 1.0)),
                "unit": canonical or raw_unit,
                "unit_resolution": "ok" if canonical else "flagged",
                "suggestion": None if canonical else suggest_unit(raw_unit),
                # Identity for merge grouping: a would-be-created item has no id yet,
                # so fall back to its name — otherwise every new item shares "None"
                # and unrelated lines would appear to merge.
                "_merge_key": str(item_id) if item_id else f"name:{(resolved_name or '').lower()}",
            }
        )

    merged = merge_duplicate_lines(
        [
            {
                "item_id": line["_merge_key"],
                "quantity_required": line["quantity"],
                "unit": line["unit"],
                "optional": bool(specs[line["index"]].get("optional", False)),
                "yield_basis": specs[line["index"]].get("yield_basis"),
                "quantity_mode": specs[line["index"]].get("quantity_mode"),
                "_indexes": [line["index"]],
                "_name": line["name"],
            }
            for line in lines
        ],
        track_indexes=True,
    )

    for row in merged:
        value = Decimal(str(row["quantity_required"]))
        if not value.is_finite() or not Decimal("0") < value <= Decimal("999999999.999"):
            errors.append(f"lines {row['_indexes']}: merged quantity exceeds positive Numeric(12,3) storage range")
        else:
            row["quantity_required"] = float(_display_amount(value))

    for line in lines:
        line.pop("_merge_key", None)

    return {
        "ingredients": lines,
        "merged_ingredients": [
            {
                "name": row["_name"],
                "from_indexes": row["_indexes"],
                "quantity_mode": row.get("quantity_mode") or "amount",
                "yield_basis": bool(row.get("yield_basis")),
                "quantity": float(row["quantity_required"]),
                "unit": row["unit"],
                "optional": bool(row["optional"]),
            }
            for row in merged
        ],
        "flagged_units": [
            {
                "index": line["index"],
                "item_name": line["name"],
                "unit": line["unit"],
                "suggestion": line["suggestion"],
            }
            for line in lines
            if line["unit_resolution"] == "flagged"
        ],
        "would_create_items": would_create_items,
        "errors": errors,
    }


def set_recipe_ingredients(db: Session, recipe_id: UUID, ingredients: list[dict]) -> list[RecipeIngredient]:
    # Lock the parent before reading or deleting children. Scoped review writes
    # use the same order, including when a removed ingredient cascades reviews.
    recipe = planning_repository.lock_recipe_for_update(db, recipe_id)
    if not recipe:
        raise LookupError("Recipe not found")

    previous = list(
        db.scalars(
            select(RecipeIngredient)
            .where(RecipeIngredient.recipe_id == recipe_id)
            .order_by(RecipeIngredient.created_at, RecipeIngredient.id)
        ).all()
    )
    # Validate the entire replacement before mutating retained rows. A bad
    # reference must not partially alter quantities or erase review history.
    for entry in ingredients:
        item = db.execute(select(Item).where(Item.id == entry["item_id"])).scalar_one_or_none()
        if not item:
            db.rollback()
            raise LookupError(f"Item not found: {entry['item_id']}")

    def values(entry, prior=None):
        return {
            "quantity_required": Decimal(str(entry["quantity_required"])),
            "unit": entry["unit"],
            "optional": bool(entry.get("optional", False)),
            "yield_basis": bool(entry["yield_basis"])
            if entry.get("yield_basis") is not None
            else bool(prior and prior.yield_basis),
            "quantity_mode": entry.get("quantity_mode") or (prior.quantity_mode if prior else None) or "amount",
        }

    remaining = previous.copy()
    retained: dict[int, RecipeIngredient] = {}
    # Match every unchanged line first. Otherwise an earlier edited duplicate
    # could steal the identity (and confirmed receipt) of an unchanged sibling.
    for index, entry in enumerate(ingredients):
        for prior in remaining:
            if prior.item_id == entry["item_id"] and all(
                getattr(prior, key) == value for key, value in values(entry, prior).items()
            ):
                retained[index] = prior
                remaining.remove(prior)
                break

    rows: list[RecipeIngredient] = []
    for index, entry in enumerate(ingredients):
        row = retained.get(index)
        if row is None:
            candidates = [prior for prior in remaining if prior.item_id == entry["item_id"]]
            if candidates:
                # The old payload has no line IDs. Prefer the existing physical
                # line shape, then the stable order established above.
                row = min(
                    candidates,
                    key=lambda prior: (
                        prior.unit != entry["unit"],
                        bool(entry.get("quantity_mode") and prior.quantity_mode != entry["quantity_mode"]),
                        prior.optional != bool(entry.get("optional", False)),
                    ),
                )
                remaining.remove(row)
            else:
                row = RecipeIngredient(recipe_id=recipe_id, item_id=entry["item_id"])
                db.add(row)
        for key, value in values(entry, row).items():
            setattr(row, key, value)
        rows.append(row)

    # Existing FK cascades remain correct for genuine removals only. Retained
    # IDs preserve review receipts/scopes; their evidence hash detects edits.
    for removed in remaining:
        db.delete(removed)

    # One commit for the whole set: a bad item_id partway through now leaves the
    # recipe's ingredients untouched instead of half-written.
    db.commit()
    for row in rows:
        db.refresh(row)

    return rows


def get_recipe_with_ingredients(db: Session, recipe_id: UUID) -> tuple[Recipe, list[dict]]:
    recipe = get_recipe(db, recipe_id)
    if not recipe:
        raise LookupError("Recipe not found")

    rows = db.execute(
        select(RecipeIngredient, Item)
        .join(Item, RecipeIngredient.item_id == Item.id)
        .where(RecipeIngredient.recipe_id == recipe_id)
        .order_by(Item.name.asc())
    ).all()

    ingredients = [
        {
            "ingredient_id": ingredient.id,
            "item_id": ingredient.item_id,
            "item_name": item.name,
            "quantity_required": str(ingredient.quantity_required),
            "unit": ingredient.unit,
            "optional": ingredient.optional,
            "yield_basis": ingredient.yield_basis,
            "quantity_mode": ingredient.quantity_mode,
        }
        for ingredient, item in rows
    ]
    return recipe, ingredients


def recipe_nutrition(db: Session, recipe_id: UUID) -> dict:
    """Fold each ingredient's per-100g macros into recipe total + per-serving totals.

    Computed on read (not stored) so it stays correct when an ingredient's macros
    are edited. Ingredients whose amount can't be converted to grams (missing
    density / grams-per-each) are listed in ``unresolved`` and excluded from the
    sum, which is then marked ``complete: false`` — an honest partial rather than a
    wrong number (ADR 0011 D2/D3).
    """
    recipe = get_recipe(db, recipe_id)
    if not recipe:
        raise LookupError("Recipe not found")

    rows = db.execute(
        select(RecipeIngredient, Item)
        .join(Item, RecipeIngredient.item_id == Item.id)
        .where(RecipeIngredient.recipe_id == recipe_id)
        .order_by(Item.name.asc())
    ).all()

    keys = ("calories", "protein_g", "fat_g", "carbs_g")
    total = {k: Decimal("0") for k in keys}
    contributed = {k: False for k in keys}
    unresolved: list[dict] = []

    for ingredient, item in rows:
        macros = macros_for_amount(db, item, Decimal(ingredient.quantity_required), ingredient.unit)
        if macros is None:
            unresolved.append(
                {
                    "item_id": item.id,
                    "item_name": item.name,
                    "reason": "no unit conversion (set density / grams-per-each) or amount not convertible",
                }
            )
            continue
        # Converted, but the item carries no macro data at all — it contributes
        # nothing, so mark it unresolved rather than letting the rollup read as
        # complete with null totals.
        if all(macros.get(k) is None for k in keys):
            unresolved.append(
                {
                    "item_id": item.id,
                    "item_name": item.name,
                    "reason": "no nutrition data (set per-100g calories/protein/fat/carbs)",
                }
            )
            continue
        for k in keys:
            value = macros.get(k)
            if value is not None:
                total[k] += Decimal(value)
                contributed[k] = True

    servings = recipe.servings or 1

    def _out(mapping: dict, divide_by: int = 1) -> dict:
        out: dict = {}
        for k in keys:
            if not contributed[k]:
                out[k] = None
            else:
                out[k] = float((mapping[k] / Decimal(divide_by)).quantize(Decimal("0.01")))
        return out

    return {
        "recipe_id": recipe.id,
        "servings": servings,
        "total": _out(total),
        "per_serving": _out(total, servings),
        "unresolved": unresolved,
        "complete": len(unresolved) == 0,
    }


def _identity_pool_item_ids(db: Session, item_id: UUID) -> list[UUID]:
    """Every active item that IS this ingredient — the item itself plus any other
    catalog record sharing its canonical ingredient key (ADR 0015). So a recipe's
    generic "cumin" draws on the "Lawry's Ground Cumin" you actually stocked.

    Falls back to just the item when it has no canonical key (nothing to pool with),
    which is the safe exact-id behaviour."""

    canon = db.execute(
        select(Item.canonical_ingredient).where(Item.id == item_id)
    ).scalar_one_or_none()
    if not canon:
        return [item_id]

    ids = db.execute(
        select(Item.id).where(Item.canonical_ingredient == canon, Item.is_active.is_(True))
    ).scalars().all()
    # item_id first so a same-key deduction is stable; de-dupe preserving order.
    return list(dict.fromkeys([item_id, *ids]))


def _scale_from_ingredient(
    db: Session,
    *,
    ingredients: list[RecipeIngredient],
    ingredient_id: UUID,
    location_id: UUID | None,
) -> Decimal:
    """The batch multiplier that spends everything on hand for one line.

    "The recipe wants 3 lb of pork, I have 4.87 lb, use it all" → 1.623, and
    every other line is scaled by it. This is the shape of cooking a
    variable-weight ingredient: you don't pick a batch size and then buy meat,
    you get handed a roast and size the batch around it.

    Deliberately uses the POOLED on-hand (ADR 0015 identity, ADR 0017 §6) so it
    agrees with what the availability screen shows and what planning will then
    spend — computing it any other way would let planning come up short against
    a scale planning itself chose.
    """
    line = next((i for i in ingredients if i.id == ingredient_id), None)
    if line is None:
        raise LookupError("That ingredient isn't part of this recipe")
    if getattr(line, "quantity_mode", "amount") == "package":
        raise ValueError(
            "That line already means 'a whole package', so there is nothing to scale it to — "
            "it takes what the package holds whatever the batch size"
        )
    required = Decimal(line.quantity_required or 0)
    if required <= 0:
        raise ValueError("Can't size a cook from a line with no amount on it")

    item = db.get(Item, line.item_id)
    if item is None:
        raise LookupError("Item not found")

    target = _comparison_unit(line.unit)
    ref_index = build_reference_index(db)
    pool_ids = _identity_pool_item_ids(db, line.item_id)
    have = Decimal("0")
    for stock in list_items_stock_fefo(db, pool_ids, location_id=location_id):
        member = db.get(Item, stock["item_id"])
        converted = convert_amount(
            db,
            member,
            Decimal(stock["quantity"]),
            from_unit=stock["unit"],
            to_unit=target,
            hints=resolve_hints(db, member, index=ref_index),
        )
        if converted is not None and converted > 0:
            have += converted

    if have <= 0:
        raise ValueError(
            f"Can't size this cook from {item.name}: there is none on hand "
            + ("at this location" if location_id is not None else "anywhere")
        )

    in_target = convert_amount(
        db, item, required, from_unit=line.unit, to_unit=target,
        hints=resolve_hints(db, item, index=ref_index),
    )
    if in_target is None or in_target <= 0:
        raise ValueError(
            f"Can't size this cook from {item.name}: {line.unit} doesn't convert to {target} — "
            + _missing_bridge_hint(line.unit, target, item.name)
        )
    return have / in_target


def _take_whole_packages(
    db: Session,
    *,
    pool_ids: list[UUID],
    location_id: UUID | None,
    wanted: Decimal,
    ingredient_id: UUID | None,
) -> tuple[list[dict], Decimal]:
    """Claim ``wanted`` whole packages, first-expiring-first. Returns the plan
    steps and how many packages were missing.

    Each step is expressed in the LOT's own unit and amount, so the deduction is
    exactly what that package holds. That is the point: an amount line has to
    name a number, and the number is wrong for every package but one. It also
    sidesteps a rounding trap — a recipe asking for "9 lb" against a lot holding
    exactly 9 lb is short by a third of a milligram, because the balance rounded
    to Numeric(12,3) on the way in and the required amount didn't.

    A fractional request (half a package) takes that fraction of the next one.
    """
    steps: list[dict] = []
    remaining = Decimal(wanted)
    for package in list_packages_fefo(db, pool_ids, location_id=location_id):
        if remaining <= 0:
            break
        share = min(remaining, Decimal("1"))
        steps.append(
            {
                "item_id": package["item_id"],
                "location_id": package["location_id"],
                "quantity": Decimal(package["quantity"]) * share,
                "unit": package["unit"],
                "ingredient_id": ingredient_id,
            }
        )
        remaining -= share
    return steps, max(Decimal("0"), remaining)


def _plan_ingredient_deductions_report(
    db: Session,
    *,
    ingredients: list[RecipeIngredient],
    location_id: UUID | None,
    scale: Decimal,
    overrides: dict[UUID, dict] | None = None,
) -> tuple[list[dict], list[dict], list[dict]]:
    """
    Resolve each ingredient to concrete (item, location, quantity) deductions.

    Stock is pooled across every catalog record that shares the ingredient's
    canonical key (ADR 0015 identity) and taken first-expiring-first — so a recipe
    that lists a generic "cumin" spends the "Lawry's Ground Cumin" jar you have,
    and (with no location pinned) the household is one pool spanning kitchen,
    garage shelf and chest freezer. With a location pinned the same pooling happens
    within that location. Each deduction names the concrete stocked item. Raises
    before anything is spent if the pooled total falls short.

    ``scale`` multiplies every recipe amount (1 = the recipe as written). See
    ``cook_recipe`` for how it's derived from either the batch multiplier or
    absolute servings.

    ``overrides`` maps ingredient ROW id → {quantity, unit, omit} for this cook
    only (ADR 0022 §2). An override's ``quantity`` is the FINAL amount — ``scale``
    is deliberately NOT applied on top of it, because the user typed it while
    looking at the already-scaled figure.

    Returns ``(plan, shortfalls, unconvertible)`` and never raises on a gap —
    the recipe-cook wrapper (``_plan_ingredient_deductions``) turns gaps into
    refusals or, when asked, into caps, while the ad hoc path (ADR 0024 §3)
    executes the partial plan and *reports* the gaps instead: the food is already
    in the pan, so the ledger doesn't get to veto reality. Each shortfall carries
    ``ingredient_id`` and ``wanted`` alongside the exact ``message`` the refusal
    path would show, which is what lets the wrapper turn one into a cap without
    re-deriving anything.
    """

    plan: list[dict] = []
    shortfalls: list[dict] = []
    unconvertible: list[dict] = []
    ref_index = build_reference_index(db)
    overrides = overrides or {}

    for ingredient in ingredients:
        override = overrides.get(getattr(ingredient, "id", None)) or {}
        if override.get("omit"):
            continue  # left out on purpose — no deduction, no shortfall check
        override_quantity = override.get("quantity")

        # An override on a to-taste line is how you opt INTO deducting it, so the
        # optional skip only applies when the amount wasn't overridden.
        # getattr: a substituted ingredient is a SimpleNamespace without `optional`.
        if override_quantity is None and getattr(ingredient, "optional", False):
            continue  # to-taste / garnish — never auto-deducted (ADR 0017 §4.1)
        line_item = db.get(Item, ingredient.item_id)
        if line_item is None:
            continue
        effective_unit = override.get("unit") or ingredient.unit
        target = _comparison_unit(effective_unit)

        # A WHOLE-PACKAGE line (ADR 0025 §5): "one roast", not "3 lb of roast".
        # Resolved against the packages actually on the shelf, because what a
        # package weighs is a fact about the lot and nothing about the catalogue
        # entry can produce it. An explicit per-line override opts back out —
        # typing an amount means you meant that amount.
        if getattr(ingredient, "quantity_mode", "amount") == "package" and override_quantity is None:
            wanted_packages = Decimal(ingredient.quantity_required or 1) * scale
            taken, short = _take_whole_packages(
                db,
                pool_ids=_identity_pool_item_ids(db, ingredient.item_id),
                location_id=location_id,
                wanted=wanted_packages,
                ingredient_id=getattr(ingredient, "id", None),
            )
            plan.extend(taken)
            if short > 0:
                where = "at this location" if location_id is not None else "across all locations"
                unit = "package" if short == 1 else "packages"
                shortfalls.append(
                    {
                        "ingredient_id": getattr(ingredient, "id", None),
                        "item_name": line_item.name,
                        "short": short,
                        "wanted": wanted_packages,
                        "unit": "package",
                        "message": (
                            f"{line_item.name} (short {_display_amount(short)} {unit} {where})"
                        ),
                    }
                )
            continue

        # Convert the recipe amount into the recipe unit's dimension canonical (ADR
        # 0017 §1/§6). Refuse up front when there's no bridge — checking here, before
        # any remove_inventory runs, is what preserves the all-or-nothing guarantee
        # (§9); discovering it mid-loop would leave the pantry half-spent.
        raw_amount = (
            Decimal(override_quantity)
            if override_quantity is not None
            else Decimal(ingredient.quantity_required) * scale
        )
        needed = convert_amount(
            db,
            line_item,
            raw_amount,
            from_unit=effective_unit,
            to_unit=target,
            hints=resolve_hints(db, line_item, index=ref_index),
        )
        if needed is None:
            unconvertible.append(
                {
                    "item_name": line_item.name,
                    "unit": effective_unit,
                    "message": (
                        f"{line_item.name} (can't convert {effective_unit} — set a density "
                        f"or package size on the item)"
                    ),
                }
            )
            continue
        if needed <= 0:
            continue  # optional / to-taste line — not auto-deducted (§4.1)

        # Pool FEFO across the identity set; each member's on-hand (in its own stored
        # unit) is converted into the comparison unit before it's drawn, and the
        # deduction is expressed in that unit so remove_inventory converts it back to
        # the member's stored unit.
        pool_ids = _identity_pool_item_ids(db, ingredient.item_id)
        remaining = needed
        blocked = False  # stock existed but couldn't be measured in the recipe's unit
        for stock in list_items_stock_fefo(db, pool_ids, location_id=location_id):
            if remaining <= 0:
                break
            member = db.get(Item, stock["item_id"])
            avail = convert_amount(
                db,
                member,
                Decimal(stock["quantity"]),
                from_unit=stock["unit"],
                to_unit=target,
                hints=resolve_hints(db, member, index=ref_index),
            )
            if avail is None:
                if Decimal(stock["quantity"]) > 0:
                    blocked = True
                continue  # member can't be pooled into this unit (§6) — skip
            if avail <= 0:
                continue
            take = min(remaining, avail)
            plan.append(
                {
                    "item_id": stock["item_id"],
                    "location_id": stock["location_id"],
                    "quantity": take,
                    "unit": target,
                    # Which recipe LINE this deduction serves. The item alone
                    # can't answer that — pooling spends a sibling product and a
                    # substitution swaps the item outright — and a derived yield
                    # has to know which deductions are its basis.
                    "ingredient_id": getattr(ingredient, "id", None),
                }
            )
            remaining -= take

        if remaining > 0:
            if blocked:
                # There IS stock, we just can't measure it in the recipe's unit —
                # report it as a conversion gap, not a bare shortfall (ADR 0017 §9).
                # Never capped: "less of it" and "we can't tell how much" are
                # different problems, and only the first is a decision the cook
                # can knowingly make.
                unconvertible.append(
                    {
                        "item_name": line_item.name,
                        "unit": effective_unit,
                        "message": (
                            f"{line_item.name} (can't measure the stock in {effective_unit} — "
                            f"set a density or package size on the item)"
                        ),
                    }
                )
            else:
                where = "at this location" if location_id is not None else "across all locations"
                shortfalls.append(
                    {
                        "ingredient_id": getattr(ingredient, "id", None),
                        "item_name": line_item.name,
                        "short": remaining,
                        "wanted": needed,
                        "unit": target,
                        "message": f"{line_item.name} (short {remaining} {target} {where})",
                    }
                )

    return plan, shortfalls, unconvertible


def _plan_ingredient_deductions(
    db: Session,
    *,
    ingredients: list[RecipeIngredient],
    location_id: UUID | None,
    scale: Decimal,
    overrides: dict[UUID, dict] | None = None,
    allow_short_ingredients: bool = False,
) -> tuple[list[dict], list[dict]]:
    """The refusing wrapper: plan every deduction, and raise before anything is
    spent if the pooled total falls short or a unit has no bridge — the recipe
    cook path's all-or-nothing guarantee (ADR 0017 §9), unchanged.

    ``allow_short_ingredients`` trades that guarantee for a CAP (ADR 0025 §7):
    a short line takes whatever is on hand and the cook goes ahead. That is a
    real cook — a bigger roast with the same jar of rub is seasoned a little
    lighter, not ruined — but it is a different dish from the one written down,
    so every capped line comes back rather than being quietly absorbed. An
    unconvertible line still raises either way: "less of it" and "we can't tell
    how much" are different problems, and only the first is a decision a cook
    can knowingly make.

    Returns ``(plan, capped)``; ``capped`` is empty unless a line was trimmed."""
    plan, shortfalls, unconvertible = _plan_ingredient_deductions_report(
        db, ingredients=ingredients, location_id=location_id, scale=scale, overrides=overrides
    )
    if unconvertible:
        raise ValueError("Can't convert units: " + "; ".join(u["message"] for u in unconvertible))
    if shortfalls and not allow_short_ingredients:
        raise ValueError("Insufficient inventory: " + "; ".join(s["message"] for s in shortfalls))

    # Capping happens HERE rather than in the reporter: the plan already carries
    # whatever could be taken, so a shortfall only ever needed a decision about
    # whether it is fatal — and this wrapper is the one caller entitled to make
    # it. The ad hoc path reads the same shortfalls and reports them instead.
    capped = [
        {
            "ingredient_id": s.get("ingredient_id"),
            "item_name": s["item_name"],
            "wanted": _display_amount(s["wanted"]),
            "took": _display_amount(Decimal(s["wanted"]) - Decimal(s["short"])),
            "unit": s["unit"],
        }
        for s in shortfalls
    ]
    return plan, capped


def _missing_bridge_hint(from_unit: str, to_unit: str, item_name: str) -> str:
    """Name the conversion hint that is actually missing (ADR 0025 §2).

    "Set a density" was the stock advice for every refusal, and it is wrong for
    most of them: density is g/ml and does not bridge mass↔count at all. Sending
    someone to fill in a number that cannot fix their problem is worse than
    silence — they fill it in, nothing changes, and they stop trusting the prompt.
    """
    dims = {unit_dimension(from_unit), unit_dimension(to_unit)}
    if dims == {"mass", "volume"}:
        return f"set a density (g/ml) on {item_name}"
    if dims == {"count", "mass"}:
        return f"set a package size in a weight unit (e.g. 1 lb), or a weight-per-each, on {item_name}"
    if dims == {"count", "volume"}:
        return f"set a package size in a volume unit (e.g. 500 ml), or a weight-per-each plus a density, on {item_name}"
    return f"set a package size or density on {item_name}"


def _basis_mass_grams(db: Session, plan: list[dict], basis_ids: set) -> Decimal:
    """The mass actually deducted for the yield-basis lines, in grams.

    Raises when a basis line's deduction can't be measured as a mass — a ratio of
    an amount we can't weigh is not a number we're entitled to invent.
    """
    ref_index = build_reference_index(db)
    total = Decimal("0")
    for step in plan:
        if step.get("ingredient_id") not in basis_ids:
            continue
        member = db.get(Item, step["item_id"])
        grams = convert_amount(
            db,
            member,
            Decimal(step["quantity"]),
            from_unit=step["unit"],
            to_unit="g",
            hints=resolve_hints(db, member, index=ref_index),
        )
        if grams is None:
            raise ValueError(
                f"Can't work out the yield: {member.name} was measured in {step['unit']}, "
                f"which doesn't convert to a weight — "
                + _missing_bridge_hint(step["unit"], "g", member.name)
            )
        total += grams
    return total


def _resolve_output(
    db: Session,
    recipe: Recipe,
    *,
    scale: Decimal,
    output_weight_grams: Decimal | None,
    plan: list[dict] | None = None,
    basis_ids: set | None = None,
) -> tuple[Decimal, str] | None:
    """What this cook produced, as (quantity, unit) — or None if it yields nothing.

    Precedence (ADR 0025 §3): a **measured** finished weight wins over the
    recipe's declared yield. ``scale`` is deliberately NOT applied on top of a
    measurement — the cook put the finished dish on a scale, not one batch of it,
    exactly as an ADR 0022 per-line override is already a final amount. A measured
    weight also stands in for a missing ``output_quantity``: "I made 4.5 lb of
    this" is a complete statement of the yield on its own.

    It wins only where it is genuinely a *measure of the yield*, i.e. where the
    output item's own base unit can hold it. For a COUNTED output the weight
    keeps its original meaning — "two loaves, which together weigh 850 g" states
    two different true things, and the loaves are what goes on the shelf. For a
    volume output with no density, grams say nothing we can store, so the
    declared yield stands and the weight stays provenance on the event. Neither
    case refuses: a weight offered alongside a yield we can already record is
    extra information, never an error.

    With nothing weighed, a ``yield_ratio`` recipe **derives** the yield from the
    mass its basis lines actually consumed, so the same recipe against a 4.87 lb
    roast and a 9 lb roast produces two different, correct numbers. That path
    DOES refuse when it can't be computed: unlike a stray weight, asking for a
    ratio is asking for this number specifically.
    """
    if recipe.output_item_id is None:
        return None
    declared = (
        (Decimal(recipe.output_quantity) * scale, recipe.output_unit or "each")
        if recipe.output_quantity is not None
        else None
    )
    item = db.get(Item, recipe.output_item_id)
    base = base_unit_of(item) if item is not None else "each"

    if output_weight_grams is not None and Decimal(output_weight_grams) > 0:
        if unit_dimension(base) == "count":
            return declared
        if item is None or convert_amount(db, item, Decimal(output_weight_grams), from_unit="g", to_unit=base) is None:
            return declared
        return Decimal(output_weight_grams), "g"

    if (recipe.output_mode or "fixed") == "yield_ratio":
        if not recipe.output_yield_ratio:
            raise ValueError(
                f"{recipe.name} yields a ratio of its ingredients but no ratio is set — "
                f"set one (e.g. 0.6 for pulled pork), or switch the recipe back to a fixed yield"
            )
        if not basis_ids:
            raise ValueError(
                f"{recipe.name} yields a ratio of its ingredients but no ingredient is marked "
                f"as the yield basis — mark the one the yield follows (the meat, not the rub)"
            )
        grams = _basis_mass_grams(db, plan or [], basis_ids) * Decimal(recipe.output_yield_ratio)
        if grams <= 0:
            raise ValueError(
                f"Can't work out the yield: nothing was deducted for {recipe.name}'s yield-basis "
                f"ingredient, so there is no mass to take a ratio of"
            )
        return grams, "g"

    return declared


def _plan_output_packing(
    db: Session,
    item: Item | None,
    *,
    quantity: Decimal,
    unit: str,
    portions: int | None,
    portion_size: Decimal | None,
    portion_unit: str | None,
) -> list[dict]:
    """Split a cook's yield into the lots it was physically stored as (ADR 0025 §4).

    Returns one entry per lot to produce. With no portioning asked for that is a
    single plain lot — today's behaviour, unchanged. Otherwise it is up to two:
    the sealed tubs, and whatever stayed loose because it went straight to the
    table. The loose lot is returned FIRST so it is produced (and therefore
    consumed) ahead of the sealed ones.

    Three ways to ask, all in the output item's base unit once resolved:
      * ``portions`` alone — split the whole yield evenly into that many tubs.
      * ``portions`` + ``portion_size`` — that many tubs of exactly that size;
        the rest stays loose.
      * ``portion_size`` alone — as many whole tubs of that size as the yield
        allows; the rest stays loose.
    """
    plain = [{"quantity": quantity, "unit": unit}]
    if not portions and portion_size is None:
        return plain
    if item is None:
        return plain

    base = base_unit_of(item)
    total = convert_amount(db, item, Decimal(quantity), from_unit=unit, to_unit=base)
    if total is None or total <= 0:
        # Unconvertible yields already store leniently in the unit given (ADR
        # 0017 §8); portioning an amount we can't measure would be a guess.
        return plain

    if portion_size is not None:
        size = convert_amount(
            db, item, Decimal(portion_size), from_unit=portion_unit or unit, to_unit=base
        )
        if size is None or size <= 0:
            raise ValueError(
                f"Can't record the portions: {portion_unit or unit} can't convert to {base} — "
                + _missing_bridge_hint(portion_unit or unit, base, item.name)
            )
        count = int(portions) if portions else int(total // size)
        if count <= 0 or size <= 0:
            return plain
        packed = size * count
        if packed > total:
            raise ValueError(
                f"Can't record the portions: {count} × {_display_amount(size)} {base} is more "
                f"than the {_display_amount(total)} {base} this cook made"
            )
    else:
        # An even split is by definition "all of it, in N tubs" — so the packed
        # amount is the whole yield, and the per-tub size is only how it reads
        # back. Deriving `packed` from a divided size instead would leave dust
        # behind (1800/7 does not multiply back to 1800 in Decimal), and dust
        # becomes a phantom second lot holding a millionth of a gram.
        count = int(portions)
        if count <= 0:
            return plain
        packed = total
        size = (total / Decimal(count)).quantize(Decimal("0.001"), rounding=ROUND_HALF_UP)

    lots: list[dict] = []
    remainder = total - packed
    # Below the precision quantities are stored at (Numeric(12,3)), a remainder
    # is a rounding artefact rather than food anyone put anywhere.
    if remainder >= Decimal("0.001"):
        lots.append({"quantity": remainder, "unit": base})
    lots.append(
        {
            "quantity": packed,
            "unit": base,
            "container_size": size,
            "container_unit": base,
            "sealed_count": count,
        }
    )
    return lots


def _primary_source_location(db: Session, plan: list[dict]) -> UUID:
    """The location a multi-location cook is attributed to: the biggest contributor."""

    totals: dict[UUID, Decimal] = {}
    for step in plan:
        totals[step["location_id"]] = totals.get(step["location_id"], Decimal("0")) + step["quantity"]

    if not totals:
        raise ValueError("Recipe has no ingredients to cook")

    return max(totals.items(), key=lambda entry: entry[1])[0]


def cook_recipe(
    db: Session,
    *,
    recipe_id: UUID,
    location_id: UUID | None,
    servings: int,
    leftover_servings: int,
    note: str | None,
    actor: str | None = None,
    output_weight_grams: Decimal | None = None,
    substitutions: list[dict] | None = None,
    servings_made: Decimal | None = None,
    ingredient_overrides: list[dict] | None = None,
    output_portions: int | None = None,
    output_portion_size: Decimal | None = None,
    output_portion_unit: str | None = None,
    scale_to_ingredient_id: UUID | None = None,
    allow_short_ingredients: bool = False,
    scoped_substitutions: list[dict] | None = None,
) -> dict:
    """Cook a recipe: deduct its ingredients, record the event, produce any output.

    ``output_weight_grams`` is what the finished dish actually weighed, and it
    **wins over the recipe's declared yield** (ADR 0025 §3) — a scale reading is a
    measurement, the recipe's `output_quantity` only a default.

    ``output_portions`` / ``output_portion_size`` say how that yield was packed:
    three tubs of leftovers, with anything left over staying loose (ADR 0025 §4).
    Opt-in per cook, with no recipe-level default — how a batch gets divided
    depends on the evening, not on the recipe.

    Three ways to say how much (ADR 0022 §1, ADR 0025 §5):
      * ``servings`` — a BATCH MULTIPLIER. 2 = "two whole recipes", deducting
        twice every amount. The original behaviour, unchanged.
      * ``servings_made`` — ABSOLUTE servings to produce. Scales by
        ``servings_made / recipe.servings``, so 2 servings of a recipe yielding
        4 deducts half. When given, it wins and ``servings`` is ignored.
      * ``scale_to_ingredient_id`` — SIZE THE COOK FROM THE PANTRY: "use all the
        pork I have". The scale becomes what's on hand for that line divided by
        what the line asks for, and every other line follows it. Wins over both
        of the above, because it is the most specific thing the cook can say.

    ``allow_short_ingredients`` lets a line that comes up short be CAPPED at
    what's on hand instead of refusing the cook (ADR 0025 §7) — a bigger roast
    with the same jar of rub, seasoned a little lighter. Every capped line comes
    back in ``capped_ingredients``: the proportions really did change, and that
    is worth saying out loud rather than absorbing.

    Leaving all of them unset reproduces the legacy behaviour exactly, so
    existing callers (including the MCP agent tool) are unaffected.
    """
    recipe = get_recipe(db, recipe_id)
    if not recipe:
        raise LookupError("Recipe not found")

    if servings_made is not None:
        scale = Decimal(servings_made) / Decimal(recipe.servings or 1)
    else:
        scale = Decimal(servings)

    if location_id is not None:
        location = get_location(db, location_id)
        if not location:
            raise LookupError("Location not found")

    ingredients = db.execute(select(RecipeIngredient).where(RecipeIngredient.recipe_id == recipe_id)).scalars().all()
    if not ingredients:
        raise ValueError("Recipe has no ingredients")

    # "Cook the roast I have" (ADR 0025 §5): the pantry sizes the batch, and the
    # rub, the sauce and the yield all follow it. Resolved before planning, since
    # it *is* the scale everything else is planned at.
    if scale_to_ingredient_id is not None:
        scale = _scale_from_ingredient(
            db,
            ingredients=ingredients,
            ingredient_id=scale_to_ingredient_id,
            location_id=location_id,
        )

    # Substitution overrides (ADR 0011 D5): choosing a substitute *learns* the
    # mapping (upsert) and swaps the ingredient for planning — the substitute is
    # then deducted (FEFO across locations when unpinned) exactly like any other
    # ingredient. Picking a substitute registers it; there is no pre-registration
    # gate. Quantity is scaled by the substitution ratio.
    override_by_original = {UUID(str(s["original_item_id"])): s for s in (substitutions or [])}
    scoped_by_row = {UUID(str(s["ingredient_id"])): s for s in (scoped_substitutions or [])}
    applied_subs: list[dict] = []
    effective_ingredients: list = []
    for ingredient in ingredients:
        scoped = scoped_by_row.get(ingredient.id)
        if scoped is not None:
            if ingredient.item_id in override_by_original or str(ingredient.item_id) != scoped["original_item_id"] or to_recipe_unit(ingredient.unit) != scoped["from_unit"] or ingredient.quantity_mode == "package":
                raise ValueError("Scoped substitution no longer matches the original ingredient row")
            effective_ingredients.append(SimpleNamespace(id=ingredient.id, item_id=UUID(scoped["substitute_item_id"]),
                quantity_required=Decimal(ingredient.quantity_required) * Decimal(scoped["ratio"]), unit=scoped["unit"],
                quantity_mode=ingredient.quantity_mode, yield_basis=ingredient.yield_basis, optional=ingredient.optional))
            applied_subs.append({**scoped, "ratio": float(scoped["ratio"])})
            continue
        override = override_by_original.get(ingredient.item_id)
        if override is None:
            effective_ingredients.append(ingredient)
            continue
        substitute_item_id = UUID(str(override["substitute_item_id"]))
        bound = cook_basis(db, ingredient.item_id, substitute_item_id, ingredient.unit)
        sub = upsert_substitution(
            db,
            from_item_id=ingredient.item_id,
            to_item_id=substitute_item_id,
            ratio=override.get("ratio"),
            unit=override.get("unit") or (bound.unit if bound else ingredient.unit),
            original_unit=ingredient.unit,
        )
        effective_ingredients.append(
            SimpleNamespace(
                # Carry the original row id so a per-line override (ADR 0022 §2)
                # still lands on a line that was also substituted.
                id=ingredient.id,
                item_id=substitute_item_id,
                quantity_required=Decimal(ingredient.quantity_required) * Decimal(sub.ratio),
                unit=sub.unit,
            )
        )
        applied_subs.append(
            {
                "original_item_id": str(ingredient.item_id),
                "substitute_item_id": str(substitute_item_id),
                "ratio": float(sub.ratio),
            }
        )

    # Work out every deduction before making any of them. remove_inventory
    # commits per call, so discovering a shortfall halfway through would leave
    # the pantry half-consumed with no cooked meal to show for it.
    override_by_ingredient = {
        UUID(str(o["ingredient_id"])): o for o in (ingredient_overrides or [])
    }
    plan, capped = _plan_ingredient_deductions(
        db,
        ingredients=effective_ingredients,
        location_id=location_id,
        scale=scale,
        overrides=override_by_ingredient,
        allow_short_ingredients=allow_short_ingredients,
    )

    # How much output this cook produced, and in what unit (ADR 0025 §3): a
    # weighed dish beats a derived ratio beats the recipe's declared yield.
    # Resolved AFTER planning — a derived yield is a function of what the plan
    # actually spends — but still before a single removal runs, so the
    # convertibility check below keeps the all-or-nothing guarantee (§9).
    output = _resolve_output(
        db,
        recipe,
        scale=scale,
        output_weight_grams=output_weight_grams,
        plan=plan,
        basis_ids={i.id for i in ingredients if getattr(i, "yield_basis", False)},
    )

    # Output convertibility (ADR 0017 §8): if the recipe yields an output, verify it
    # converts to the output item's base unit BEFORE any deduction. A cross-dimension
    # gap (e.g. producing "1 cup" into a grams item with no density) would otherwise
    # store a mixed-unit balance the projector sums nonsensically. Same-dimension /
    # count tokens (e.g. "loaf" → each) still pass through the lenient store.
    if output is not None:
        out_item = db.get(Item, recipe.output_item_id)
        out_quantity, out_unit = output
        out_base = base_unit_of(out_item)
        if (
            convert_amount(db, out_item, out_quantity, from_unit=out_unit, to_unit=out_base) is None
            and unit_dimension(out_unit) != unit_dimension(out_base)
        ):
            raise ValueError(
                f"Can't record the output: {out_unit} can't convert to {out_base} — "
                + _missing_bridge_hint(out_unit, out_base, out_item.name)
            )

    # How the yield will be PACKED (ADR 0025 §4) — tubs of leftovers, plus
    # whatever stays loose in the serving dish. Opt-in per cook: with no
    # portioning asked for, this is one plain lot exactly as before. Worked out
    # before any removal too: portions that don't fit the yield must fail the
    # cook while the pantry is still untouched.
    packing: list[dict] = []
    if output is not None:
        packing = _plan_output_packing(
            db,
            get_item(db, recipe.output_item_id),
            quantity=output[0],
            unit=output[1],
            portions=output_portions,
            portion_size=output_portion_size,
            portion_unit=output_portion_unit,
        )

    tx_ids: list[UUID] = []
    for step in plan:
        tx, _row, _item = remove_inventory(
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

    # Where the output lands, and what the cook event is stamped with, when the
    # cook drew from several locations: whichever contributed most.
    if location_id is None:
        location_id = _primary_source_location(db, plan)

    # Transformation output (ADR 0003 D3): if the recipe declares an output item,
    # cooking *produces* a tracked lot of it — the cook's actor, in the same
    # ledger/transaction as the consumption. No output → backward-compatible.
    produced: dict | None = None
    if output is not None:
        produced_quantity, produced_unit = output
        # Leftovers get a best-by even though nobody typed one. Without it a
        # cooked dish enters inventory undated and never reaches the
        # expiring-soon list, so the thing that most needs eating first is the
        # one thing that never gets surfaced. An output item with its own
        # configured shelf life (a dish someone deliberately freezes, say)
        # keeps it; everything else takes the leftovers convention.
        output_item = get_item(db, recipe.output_item_id)
        output_shelf_life = (
            output_item.default_expiration_days
            if output_item is not None and output_item.default_expiration_days
            else LEFTOVER_SHELF_LIFE_DAYS
        )
        best_by = date.today() + timedelta(days=output_shelf_life)

        produced_row = None
        # The loose remainder is produced FIRST so it carries the lower sequence
        # and is drawn before a sealed tub is opened — dinner comes off the
        # serving dish, not out of the fridge (see _consume_containers).
        for part in packing:
            _event, produced_row, _item = produce_inventory(
                db,
                item_id=recipe.output_item_id,
                location_id=location_id,
                quantity=part["quantity"],
                unit=part["unit"],
                expiration_date=best_by,
                reason="recipe_yield",
                note=f"recipe:{recipe_id}",
                actor=actor,
                weight_grams=output_weight_grams,
                container_size=part.get("container_size"),
                container_unit=part.get("container_unit"),
                sealed_count=part.get("sealed_count"),
            )
        produced = {
            "produced_item_id": recipe.output_item_id,
            "produced_quantity": produced_quantity,
            "produced_unit": produced_unit,
            "produced_balance": Decimal(produced_row.quantity) if produced_row is not None else Decimal("0"),
        }
        portioned = next((p for p in packing if p.get("sealed_count")), None)
        if portioned is not None:
            produced["produced_portions"] = int(portioned["sealed_count"])
            produced["produced_portion_size"] = Decimal(portioned["container_size"])
            produced["produced_portion_unit"] = portioned["container_unit"]
            loose = next((p for p in packing if not p.get("sealed_count")), None)
            produced["produced_loose"] = Decimal(loose["quantity"]) if loose is not None else Decimal("0")

    event = RecipeCookEvent(
        recipe_id=recipe_id,
        location_id=location_id,
        servings=servings,
        leftover_servings=leftover_servings,
        note=note,
        substitutions=applied_subs or None,
        servings_made=Decimal(servings_made) if servings_made is not None else None,
        # Stringified ids/amounts: this is a JSONB audit record, not a rehydrated
        # object, and it has to stay readable if the ingredient row is later deleted.
        ingredient_overrides=[
            {
                "ingredient_id": str(o["ingredient_id"]),
                "quantity": None if o.get("quantity") is None else float(o["quantity"]),
                "unit": o.get("unit"),
                "omit": bool(o.get("omit", False)),
            }
            for o in (ingredient_overrides or [])
        ]
        or None,
    )
    db.add(event)
    db.commit()
    db.refresh(event)

    result = {
        "cook_event_id": event.id,
        "recipe_id": recipe_id,
        "location_id": location_id,
        "deducted_transaction_ids": tx_ids,
        "leftover_servings": leftover_servings,
        # What the batch was actually sized at. Worth returning because with
        # scale_to_ingredient the caller didn't choose it — the pantry did.
        "scale_used": _display_amount(scale),
        # Lines that were trimmed to what was on the shelf. The dish that came
        # out is not quite the one written down, and the only dishonest thing to
        # do with that fact is keep it.
        "capped_ingredients": capped,
    }
    if produced is not None:
        result.update(produced)
    return result


def cook_events_since(db: Session, *, since: datetime | None, limit: int = 200) -> list[dict]:
    """Cooks recorded at or after ``since``, oldest first (ADR 0026 §4).

    The published read for integrations; ``since=None`` returns nothing so a
    first connection never replays history. Callers de-duplicate on
    ``cook_event_id`` — the boundary is inclusive on purpose, so two cooks in
    the same instant are never split across reads and lost.
    """
    if since is None:
        return []
    rows = planning_repository.cook_events_with_recipe_since(db, since=since, limit=limit)
    return [
        {
            "cook_event_id": event.id,
            "recipe_id": event.recipe_id,
            "recipe_name": name,
            "servings": event.servings,
            "servings_made": event.servings_made,
            "leftover_servings": event.leftover_servings,
            "created_at": event.created_at,
        }
        for event, name in rows
    ]


def suggest_recipes(db: Session, *, location_id: UUID | None = None) -> list[dict]:
    """
    Rank recipes by how much of each you can already make.

    ``location_id=None`` pools stock across every location, which is the useful
    default for a household: ingredients for one meal are rarely all in the same
    cupboard, and scoring per-location makes almost everything look unmakeable.
    """

    if location_id is not None:
        if not get_location(db, location_id):
            raise LookupError("Location not found")
        inventory_rows = db.execute(select(Inventory).where(Inventory.location_id == location_id)).scalars().all()
    else:
        inventory_rows = db.execute(select(Inventory)).scalars().all()

    # Pool on-hand by exact item (with its stored unit) and by canonical key, so a
    # recipe's generic "cumin" counts the "Lawry's Ground Cumin" you actually stocked
    # (ADR 0015 identity). canonical_ingredient=None items only count for themselves.
    canon_by_item: dict[UUID, str | None] = dict(
        db.execute(select(Item.id, Item.canonical_ingredient)).all()
    )
    items_by_id: dict[UUID, Item] = {i.id: i for i in db.execute(select(Item)).scalars().all()}
    qty_by_item: dict[UUID, Decimal] = {}
    unit_by_item: dict[UUID, str] = {}
    for row in inventory_rows:
        qty = Decimal(row.quantity)
        qty_by_item[row.item_id] = qty_by_item.get(row.item_id, Decimal("0")) + qty
        unit_by_item[row.item_id] = row.unit

    subs_by_from = canonical_subs_by_from(db)
    ref_index = build_reference_index(db)
    recipes = list_recipes(db)
    suggestions: list[dict] = []

    for recipe in recipes:
        ingredients = db.execute(
            select(RecipeIngredient, Item)
            .join(Item, RecipeIngredient.item_id == Item.id)
            .where(RecipeIngredient.recipe_id == recipe.id)
        ).all()

        if not ingredients:
            continue

        available_count = 0
        scoreable = 0
        missing_items: list[str] = []
        substitutions_used: list[dict] = []
        for ingredient, item in ingredients:
            if ingredient.optional or Decimal(ingredient.quantity_required) <= 0:
                continue  # optional / to-taste — out of the score (ADR 0017 §4.1)
            scoreable += 1

            # Compare in the recipe unit's dimension (ADR 0017 §1/§6): the need and
            # each pooled on-hand contributor convert into that unit. No bridge → can't
            # confirm you can make it, so it counts against the score.
            canon = canon_by_item.get(ingredient.item_id)
            target = _comparison_unit(ingredient.unit)
            needed = convert_amount(
                db, item, Decimal(ingredient.quantity_required),
                from_unit=ingredient.unit, to_unit=target,
                hints=resolve_hints(db, item, index=ref_index),
            )
            pool_ids = (
                [mid for mid in qty_by_item if canon_by_item.get(mid) == canon]
                if canon
                else [ingredient.item_id]
            )
            available, _raw, _bridged = _pooled_have(
                db, target_unit=target, pool_ids=pool_ids, qty_by_item=qty_by_item,
                unit_by_item=unit_by_item, items_by_id=items_by_id, ref_index=ref_index,
            )
            if needed is not None and available >= needed:
                available_count += 1
                continue

            # Substitute-aware (ADR 0015 §4 / ADR 0017 §7): an ingredient still counts
            # as available if an APPROVED substitute is on hand in sufficient quantity
            # (needed × ratio), measured in the substitute's unit.
            best = _best_sub(
                db, canon=canon, recipe_needed=Decimal(ingredient.quantity_required),
                recipe_unit=ingredient.unit, subs_by_from=subs_by_from, approved_only=True,
                canon_by_item=canon_by_item, qty_by_item=qty_by_item, unit_by_item=unit_by_item,
                items_by_id=items_by_id, ref_index=ref_index,
            )
            if best is not None:
                substitute, _have = best
                available_count += 1
                substitutions_used.append(
                    {
                        "missing_item": item.name,
                        "substitute_item": substitute.to_label or substitute.to_canonical,
                        "outcome": substitute.outcome,
                    }
                )
            else:
                missing_items.append(item.name)

        score = available_count / scoreable if scoreable else 1.0
        suggestions.append(
            {
                "recipe_id": recipe.id,
                "recipe_name": recipe.name,
                "available_score": score,
                "missing_items": missing_items,
                "substitutions_used": substitutions_used,
            }
        )

    suggestions.sort(key=lambda row: row["available_score"], reverse=True)
    return suggestions


def recipe_availability(
    db: Session,
    recipe_id: UUID,
    *,
    location_id: UUID | None = None,
    servings_made: Decimal | None = None,
) -> dict:
    """Per-ingredient availability for the recipe view (ADR 0015 Phase 2).

    Each ingredient is one of: ``have`` (an item with the same canonical key is on
    hand in sufficient quantity — equality/alias identity), ``suggested`` (not on
    hand, but a tight-subset product IS — e.g. the recipe's "cheddar cheese" and
    your "shredded cheddar cheese"; offered to confirm, never auto-counted), or
    ``missing``. Confirming a suggestion aliases the product so it becomes ``have``
    everywhere, forever.

    ``servings_made`` scales the required amounts to an absolute serving count
    (ADR 0022 §7), so the cook-along setup screen shows have/short for the batch
    you're actually making rather than the recipe as written. Omitted = as written.
    """
    recipe = get_recipe(db, recipe_id)
    if not recipe:
        raise LookupError("Recipe not found")
    scale = (
        Decimal(servings_made) / Decimal(recipe.servings or 1)
        if servings_made is not None
        else Decimal(1)
    )

    if location_id is not None:
        if not get_location(db, location_id):
            raise LookupError("Location not found")
        inventory_rows = db.execute(select(Inventory).where(Inventory.location_id == location_id)).scalars().all()
    else:
        inventory_rows = db.execute(select(Inventory)).scalars().all()

    items_by_id: dict[UUID, Item] = {i.id: i for i in db.execute(select(Item)).scalars().all()}
    canon_by_item = {iid: it.canonical_ingredient for iid, it in items_by_id.items()}
    name_by_item = {iid: it.name for iid, it in items_by_id.items()}

    qty_by_item: dict[UUID, Decimal] = {}
    unit_by_item: dict[UUID, str] = {}
    for row in inventory_rows:
        qty = Decimal(row.quantity)
        qty_by_item[row.item_id] = qty_by_item.get(row.item_id, Decimal("0")) + qty
        unit_by_item[row.item_id] = row.unit
    on_hand_item_ids = [iid for iid, qty in qty_by_item.items() if qty > 0]

    subs_by_from = canonical_subs_by_from(db)

    ingredients = db.execute(
        select(RecipeIngredient, Item)
        .join(Item, RecipeIngredient.item_id == Item.id)
        .where(RecipeIngredient.recipe_id == recipe_id)
        .order_by(Item.name.asc())
    ).all()

    ref_index = build_reference_index(db)
    rows: list[dict] = []
    for ingredient, item in ingredients:
        recipe_needed = Decimal(ingredient.quantity_required) * scale
        base_unit = base_unit_of(item)
        hints = resolve_hints(db, item, index=ref_index)
        canon = canon_by_item.get(ingredient.item_id)

        # Compare in the recipe unit's dimension (ADR 0017 §1/§6): convert the need
        # and each pooled contributor into that unit. `have` is the pooled on-hand in
        # that unit; `raw_have` whether ANY stock exists; `bridged` whether every
        # contributor converted (a False flags the can't-tell case).
        target = _comparison_unit(ingredient.unit)
        needed = (
            convert_amount(db, item, recipe_needed, from_unit=ingredient.unit, to_unit=target, hints=hints)
            if recipe_needed > 0
            else None
        )
        pool_ids = _pool_for_canon(canon, ingredient.item_id, qty_by_item, canon_by_item)
        have, raw_have, bridged = _pooled_have(
            db, target_unit=target, pool_ids=pool_ids, qty_by_item=qty_by_item,
            unit_by_item=unit_by_item, items_by_id=items_by_id, ref_index=ref_index,
        )

        # A whole-package line is counted in PACKAGES, not measured (ADR 0025 §5).
        # Left on the amount path it would convert the pooled grams back through
        # the item's nominal package size — the very number that is a fiction for
        # a variable-weight product, and the reason this mode exists.
        is_package_line = getattr(ingredient, "quantity_mode", "amount") == "package"
        if is_package_line:
            target = "package"
            packages = list_packages_fefo(db, _identity_pool_item_ids(db, ingredient.item_id))
            have = raw_have = Decimal(len(packages))
            needed = recipe_needed if recipe_needed > 0 else None
            bridged = True  # counting things needs no conversion to fail at
        sub_kwargs = dict(
            recipe_needed=recipe_needed, recipe_unit=ingredient.unit, canon_by_item=canon_by_item,
            qty_by_item=qty_by_item, unit_by_item=unit_by_item, items_by_id=items_by_id, ref_index=ref_index,
        )

        suggestions: list[dict] = []
        substitution_suggestions: list[dict] = []
        available_via: dict | None = None

        if ingredient.optional or recipe_needed <= 0:
            # Optional / to-taste line — informational, never a gap (ADR 0017 §4.1).
            status = "optional"
        elif needed is not None and have >= needed:
            status = "have"
        elif (
            best := _best_sub(db, canon=canon, subs_by_from=subs_by_from, approved_only=True, **sub_kwargs)
        ) is not None:
            # An approved substitute is on hand → available via substitute (ADR 0015
            # §4 step 3 / ADR 0017 §7), measured in the substitute's unit.
            approved, approved_have = best
            status = "substitute"
            available_via = {
                "to_label": approved.to_label or approved.to_canonical,
                "to_canonical": approved.to_canonical,
                "ratio": str(approved.ratio),
                "outcome": approved.outcome,
                "on_hand": str(approved_have),
            }
        else:
            # Tight-subset identity products that are on hand (ADR 0015 §2). Skip any
            # already sharing this key (that's the identity pool, not a suggestion).
            for candidate_id in on_hand_item_ids:
                product_key = canon_by_item.get(candidate_id)
                if product_key and product_key != canon and subset_suggestion(canon, product_key):
                    suggestions.append(
                        {
                            "item_id": candidate_id,
                            "item_name": name_by_item.get(candidate_id, str(candidate_id)),
                            "canonical_ingredient": product_key,
                            "canonical_provenance": items_by_id[candidate_id].canonical_provenance,
                            "canonical_version": items_by_id[candidate_id].canonical_version,
                            "canonical_revision": items_by_id[candidate_id].canonical_revision,
                            "on_hand": str(qty_by_item.get(candidate_id, Decimal("0"))),
                        }
                    )
            # Seeded (unapproved) subs whose ``to`` is on hand → confirmable
            # substitution suggestions (ADR 0015 §4 step 4), measured in the recipe's
            # unit (ADR 0017 §7).
            for sub in subs_by_from.get(canon, []) if canon else []:
                if sub.approved or not sub.to_canonical:
                    continue
                ok, sub_have = _sub_available(db, sub=sub, **sub_kwargs)
                if ok:
                    substitution_suggestions.append(
                        {
                            "substitution_id": sub.id,
                            "to_label": sub.to_label or sub.to_canonical,
                            "to_canonical": sub.to_canonical,
                            "ratio": str(sub.ratio),
                            "outcome": sub.outcome,
                            "on_hand": str(sub_have),
                        }
                    )
            # Precedence (ADR 0017 §4): suggestions first; then missing when there's
            # nothing on hand at all; then can't-convert whenever the need can't be
            # expressed OR the pool isn't fully bridged (an unconvertible contributor
            # could cover the shortfall — cook refuses the same case); else a genuine
            # short (everything measured, just not enough).
            if suggestions or substitution_suggestions:
                status = "suggested"
            elif raw_have <= 0:
                status = "missing"
            elif needed is None or not bridged:
                status = "cant_convert"
            else:
                status = "missing"

        # On-hand rendered in the recipe's own unit for display (ADR 0017 §5); None
        # when the reverse convert has no bridge (the client falls back to base).
        on_hand_recipe_unit = (
            have
            if is_package_line
            else (
                convert_amount(db, item, have, from_unit=target, to_unit=ingredient.unit, hints=hints)
                if have > 0
                else Decimal("0")
            )
        )

        suggestions.sort(key=lambda s: s["item_name"])
        substitution_suggestions.sort(key=lambda s: _OUTCOME_RANK.get(s["outcome"], 99))
        rows.append(
            {
                "ingredient_id": ingredient.id,
                "item_id": ingredient.item_id,
                "item_name": item.name,
                "canonical_ingredient": canon,
                "canonical_provenance": item.canonical_provenance,
                "canonical_version": item.canonical_version,
                "canonical_revision": item.canonical_revision,
                # Quantized for display: dividing by a base yield produces
                # Decimal's full 28-digit expansion (a third of 1 each →
                # 0.3333...333), which is unreadable and unusable in a number
                # input. Cosmetic only — the deduction recomputes the exact
                # amount from the recipe at cook time (or uses what the user typed).
                "quantity_required": str(_display_amount(recipe_needed)),
                # A package line reads as what it is — "1 package", not "1 each"
                # of a weight the catalogue only guessed at.
                "unit": "package" if is_package_line else ingredient.unit,
                "quantity_mode": ingredient.quantity_mode,
                "on_hand": str(raw_have),
                "base_unit": base_unit,
                "on_hand_recipe_unit": None if on_hand_recipe_unit is None else str(on_hand_recipe_unit),
                "status": status,
                "available_via": available_via,
                "suggestions": suggestions,
                "substitution_suggestions": substitution_suggestions,
            }
        )

    return {"recipe_id": recipe_id, "ingredients": rows}


def pending_ingredient_matches(db: Session) -> list[dict]:
    """Every recipe ingredient that is probably already on the shelf under a
    different name — the household-wide reconciliation queue (ADR 0015 §5).

    Per-recipe availability already surfaces these, but only one recipe at a
    time, so the same "is your Organic Virgin Coconut Oil the recipe's coconut
    oil?" question reappears on every recipe that uses it. Confirming rewrites
    the PRODUCT's canonical key, which fixes every recipe at once — so the
    useful shape is one list, deduplicated by ingredient, answered once.

    Only ingredients with NO exact identity match are offered (if the pantry
    already satisfies it, there is nothing to reconcile), and only against
    stock that is actually on hand — suggesting a link to something you don't
    have wouldn't change any recipe's availability.
    """
    on_hand_ids = {
        row.item_id
        for row in db.execute(select(Inventory).where(Inventory.quantity > 0)).scalars().all()
    }
    if not on_hand_ids:
        return []

    items_by_id: dict[UUID, Item] = {i.id: i for i in db.execute(select(Item)).scalars().all()}
    stocked = [items_by_id[i] for i in on_hand_ids if i in items_by_id and items_by_id[i].canonical_ingredient]
    stocked_keys = {i.canonical_ingredient for i in stocked}

    rows = db.execute(
        select(RecipeIngredient, Recipe)
        .join(Recipe, Recipe.id == RecipeIngredient.recipe_id)
        # A draft's ingredient list is still being typed mid-cook (ADR 0024 §1);
        # asking the household to reconcile against it would churn the queue.
        .where(Recipe.is_draft.is_(False))
        .order_by(Recipe.name.asc())
    ).all()

    by_item: dict[UUID, dict] = {}
    discovery_misses: set[UUID] = set()
    for ingredient, recipe in rows:
        item = items_by_id.get(ingredient.item_id)
        key = item.canonical_ingredient if item else None
        if not key or key in stocked_keys:
            continue  # unknown, or already satisfied exactly — nothing to ask
        if ingredient.item_id in discovery_misses:
            continue

        entry = by_item.get(ingredient.item_id)
        if entry is None:
            from modules.catalog.services import ranked_match_candidates

            # Broader review discovery never changes availability or establishes
            # identity. Keep obvious blend/flavor/negation distractors out of
            # this manual queue; explicit ranked review still explains them.
            try:
                discovered = ranked_match_candidates.discover(db, ingredient.item_id)
            except LookupError:
                # Archiving preserves recipe references. An unavailable target
                # must not hide unrelated household questions; the same applies
                # if a discovery source is archived during this read.
                discovery_misses.add(ingredient.item_id)
                continue
            extra_ids = {
                candidate["item"]["item_id"]
                for candidate in discovered["candidates"]
                if not (
                    (ranked_match_candidates.tokens(candidate["item"]["name"]) - ranked_match_candidates.tokens(item.name))
                    & ranked_match_candidates.DISTRACTORS
                )
            }
            candidates = [
                {
                    "item_id": p.id,
                    "item_name": p.name,
                    "canonical_ingredient": p.canonical_ingredient,
                    "canonical_provenance": p.canonical_provenance,
                    "canonical_version": p.canonical_version,
                    "canonical_revision": p.canonical_revision,
                }
                for p in stocked
                if subset_suggestion(key, p.canonical_ingredient) or str(p.id) in extra_ids
            ]
            if not candidates:
                discovery_misses.add(ingredient.item_id)
                continue
            entry = {
                # Any one row is enough to confirm against — the write targets
                # the product's key, so it applies to all of them.
                "ingredient_id": ingredient.id,
                "recipe_id": recipe.id,
                "item_id": ingredient.item_id,
                "item_name": item.name,
                "canonical_ingredient": key,
                "canonical_provenance": item.canonical_provenance,
                "canonical_version": item.canonical_version,
                "canonical_revision": item.canonical_revision,
                "recipe_names": [],
                "candidates": candidates,
            }
            by_item[ingredient.item_id] = entry
        if recipe.name not in entry["recipe_names"]:
            entry["recipe_names"].append(recipe.name)

    return sorted(by_item.values(), key=lambda e: e["item_name"].lower())


def confirm_ingredient_item(db: Session, *, ingredient_id: UUID, item_id: UUID,
                            expected_key=None, expected_revision=None, check_key=False,
                            expected_ingredient_key=None, check_ingredient_key=False,
                            replace_confirmed=False, commit=True) -> Item:
    """Confirm that a product IS a recipe's ingredient (ADR 0015 §5): set the
    product item's canonical key to the ingredient's, a learned alias that makes it
    identity-match this ingredient everywhere from now on."""
    row = db.execute(
        select(RecipeIngredient, Item)
        .join(Item, RecipeIngredient.item_id == Item.id)
        .where(RecipeIngredient.id == ingredient_id)
    ).one_or_none()
    if row is None:
        raise LookupError("Recipe ingredient not found")
    _ingredient, recipe_item = row

    key = recipe_item.canonical_ingredient
    if not key:
        raise ValueError("This ingredient has no canonical key to match to")

    from modules.catalog.services.identity import IdentityConflict, confirm_identity

    if check_ingredient_key and key != expected_ingredient_key:
        raise IdentityConflict("Ingredient identity changed since review. Refresh and review again.")
    product = confirm_identity(db, item_id=item_id, key=key,
        version=recipe_item.canonical_version, replace_confirmed=replace_confirmed,
        expected_key=expected_key, expected_revision=expected_revision, check_key=check_key)
    if commit:
        db.commit()
        db.refresh(product)
    return product
