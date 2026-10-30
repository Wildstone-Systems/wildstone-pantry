from __future__ import annotations

import re
from datetime import datetime, timezone
from decimal import Decimal
from uuid import UUID

from sqlalchemy import bindparam, func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import BarcodeAlias, Category, Item
from app.services import provider_keys
from modules.catalog.services.barcode_providers import (
    ProductResult,
    ProviderRateLimited,
    estimate_nutrition_by_name,
    lookup_external,
    lookup_external_merged,
    search_nutrition_candidates,
    usda_food_by_fdc_id,
    usda_food_portion_grams_per_each,
)
from modules.catalog.services.canonical import CANONICAL_VERSION, canonical_ingredient
from modules.catalog.services.identity import IdentityConflict, change_identity
from modules.catalog.services.units import infer_stock_mode


def _external_keys(db: Session) -> dict:
    """Resolve provider keys (DB-over-env) to pass into the lookup cascade."""
    creds = provider_keys.kroger_credentials(db)
    return {
        "usda_api_key": provider_keys.usda_api_key(db),
        "kroger": creds if all(creds) else None,
    }


def normalize_barcode(value: str) -> str:
    digits = re.sub(r"\D", "", value or "")
    if digits:
        return digits
    return (value or "").strip().lower()


def barcode_variants(value: str) -> list[str]:
    """
    The same product code written at every GTIN width.

    A scanner reads the 12 digits printed on a US pack (UPC-A) while Open Food
    Facts returns the 13-digit EAN with a leading zero. They are the same
    product, so a lookup for one must find an item stored under the other —
    otherwise the item is invisible to the scanner that created it and every
    re-scan tries to insert a duplicate barcode.
    """

    normalized = normalize_barcode(value)
    if not normalized.isdigit():
        return [normalized]

    core = normalized.lstrip("0") or "0"
    # Guard against short internal codes colliding once zero-padded.
    if not 6 <= len(core) <= 14:
        return [normalized]

    variants = {normalized, core}
    for width in (12, 13, 14):
        if len(core) <= width:
            variants.add(core.zfill(width))
    return sorted(variants)


def resolve_local_item(db: Session, barcode: str) -> Item | None:
    """Resolve a barcode to an existing item using ONLY the local DB (item
    barcode or alias) — no external provider calls. Used by the scan station's
    fast add/consume path (step 3)."""
    normalized = normalize_barcode(barcode)
    if not normalized:
        return None
    variants = barcode_variants(normalized)
    item = db.execute(select(Item).where(Item.barcode.in_(variants))).scalars().first()
    if item:
        return item
    alias = db.execute(select(BarcodeAlias).where(BarcodeAlias.barcode.in_(variants))).scalars().first()
    if alias:
        return db.execute(select(Item).where(Item.id == alias.item_id)).scalar_one_or_none()
    return None


def lookup_barcode(db: Session, barcode: str, auto_create: bool = False) -> dict:
    """Look up a barcode: local DB first, then external providers.

    When auto_create=True and the barcode is found externally but not in the
    local DB, a new Item is automatically created with the retrieved data.
    """
    normalized = normalize_barcode(barcode)

    # --- Local DB check (instant, no API call) ---
    variants = barcode_variants(normalized)
    direct_item = db.execute(select(Item).where(Item.barcode.in_(variants))).scalars().first()
    if direct_item:
        _backfill_structured_macros(db, direct_item)
        if auto_create:
            _reconcile_if_unenriched(db, direct_item, normalized)
        return {
            "barcode": normalized,
            "found": True,
            "item_id": direct_item.id,
            "item_name": direct_item.name,
            "source": "item.barcode",
            "item": _item_to_dict(direct_item),
        }

    alias = db.execute(select(BarcodeAlias).where(BarcodeAlias.barcode.in_(variants))).scalars().first()
    if alias:
        item = db.execute(select(Item).where(Item.id == alias.item_id)).scalar_one_or_none()
        if item:
            _backfill_structured_macros(db, item)
            if auto_create:
                _reconcile_if_unenriched(db, item, normalized)
            return {
                "barcode": normalized,
                "found": True,
                "item_id": item.id,
                "item_name": item.name,
                "source": "barcode_alias",
                "item": _item_to_dict(item),
            }

    # --- External lookup cascade ---
    try:
        result = lookup_external(normalized, **_external_keys(db))
    except ProviderRateLimited:
        result = None  # scan degrades to "not found"; a manual rescan can retry
    if not result:
        return {"barcode": normalized, "found": False, "source": "none"}

    if auto_create:
        item = _create_item_from_result(db, result, scanned_barcode=normalized)
        return {
            "barcode": normalized,
            "found": True,
            "item_id": item.id,
            "item_name": item.name,
            "source": f"external:{result.source}",
            "item": _item_to_dict(item),
            "product": _product_to_dict(result),
        }

    # Return external data without creating an item
    return {
        "barcode": normalized,
        "found": True,
        "item_id": None,
        "item_name": result.name,
        "source": f"external:{result.source}",
        "item": None,
        "product": _product_to_dict(result),
    }


def register_alias(db: Session, barcode: str, item_id: UUID) -> BarcodeAlias:
    normalized = normalize_barcode(barcode)

    item = db.execute(select(Item).where(Item.id == item_id)).scalar_one_or_none()
    if not item:
        raise LookupError("Item not found")

    existing = db.execute(select(BarcodeAlias).where(BarcodeAlias.barcode == normalized)).scalar_one_or_none()
    if existing:
        existing.item_id = item_id
        db.add(existing)
        db.commit()
        db.refresh(existing)
        return existing

    alias = BarcodeAlias(barcode=normalized, item_id=item_id)
    db.add(alias)
    db.commit()
    db.refresh(alias)
    return alias


def find_item_by_name(db: Session, name: str) -> Item | None:
    """Resolve a free-text ingredient name to an existing catalog item.

    Exact name first, then the ADR 0015 canonical identity key — a normalised,
    brand/size-stripped form. Without the second step every recipe import and
    every agent write minted a fresh generic item: "Parmesan cheese" would not
    match the "parmesan" a previous import created, nor the "Kraft Grated
    Parmesan Cheese 8 oz" already in the pantry, so the catalog silently filled
    up with near-duplicates that then fragment stock and nutrition.

    Only ever matches on a key that is identity-bearing: ``canonical_ingredient``
    returns None for a name that was all brand/size, and a None key matches
    nothing (safe degradation to exact-id behaviour).

    Prefers an ACTIVE item, and among several, the one whose name is shortest —
    the generic "parmesan cheese" over "parmesan cheese, shredded, family size",
    since a recipe line naming a bare ingredient means the generic.
    """
    cleaned = (name or "").strip()
    if not cleaned:
        return None

    exact = db.execute(
        select(Item).where(func.lower(Item.name) == cleaned.lower(), Item.is_active.is_(True))
    ).scalars().first()
    if exact is not None:
        return exact
    # Fall back to an inactive exact match before widening — an archived item is
    # still a better answer than inventing a new one.
    exact_any = db.execute(select(Item).where(func.lower(Item.name) == cleaned.lower())).scalars().first()
    if exact_any is not None:
        return exact_any

    key = canonical_ingredient(cleaned)
    if not key:
        return None
    matches = db.execute(
        select(Item).where(Item.canonical_ingredient == key, Item.is_active.is_(True))
    ).scalars().all()
    if not matches:
        return None
    return min(matches, key=lambda i: (len(i.name), i.name.lower()))


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _item_to_dict(item: Item) -> dict:
    """Serialise an Item model to a dict for API responses."""
    from modules.catalog.schemas.item import ItemRead

    return {
        "id": str(item.id),
        "name": item.name,
        "brand": item.brand,
        "barcode": item.barcode,
        "barcode_type": item.barcode_type,
        "image_url": item.image_url,
        "size_description": item.size_description,
        "size_quantity": float(item.size_quantity) if item.size_quantity is not None else None,
        "size_unit": item.size_unit,
        "stock_mode": item.stock_mode,
        "nutrition": item.nutrition,
        "source": item.source,
        "category_id": str(item.category_id) if item.category_id else None,
        "default_expiration_days": item.default_expiration_days,
        "energy_kcal_100g": _to_float(item.energy_kcal_100g),
        "protein_g_100g": _to_float(item.protein_g_100g),
        "fat_g_100g": _to_float(item.fat_g_100g),
        "carbs_g_100g": _to_float(item.carbs_g_100g),
        "fiber_g_100g": _to_float(item.fiber_g_100g),
        "sugars_g_100g": _to_float(item.sugars_g_100g),
        "saturated_fat_g_100g": _to_float(item.saturated_fat_g_100g),
        "sodium_g_100g": _to_float(item.sodium_g_100g),
        "serving_size_g": _to_float(item.serving_size_g),
        "ingredients_text": item.ingredients_text,
        "allergens": item.allergens,
        "nutri_score": item.nutri_score,
        "nova_group": item.nova_group,
        "eco_score": item.eco_score,
        "nutrition_estimated": item.nutrition_estimated,
        "nutrition_source": item.nutrition_source,
        "nutrition_reference": ItemRead.model_validate(item).nutrition_reference.model_dump(),
    }


def _to_float(value) -> float | None:
    return float(value) if value is not None else None


def _product_to_dict(result: ProductResult) -> dict:
    """Serialise a ProductResult to a dict for API responses."""
    return {
        "name": result.name,
        "brand": result.brand,
        "category": result.category,
        "size_description": result.size_description,
        "size_quantity": result.size_quantity,
        "size_unit": result.size_unit,
        "image_url": result.image_url,
        "nutrition": result.nutrition,
        "source": result.source,
        "serving_size_g": result.serving_size_g,
        "density_g_per_ml": result.density_g_per_ml,
        "grams_per_each": result.grams_per_each,
        "ingredients_text": result.ingredients_text,
        "allergens": result.allergens,
        "nutri_score": result.nutri_score,
        "nova_group": result.nova_group,
        "eco_score": result.eco_score,
    }


def _resolve_or_create_category(db: Session, category_name: str | None) -> UUID | None:
    if not category_name:
        return None
    cat = db.execute(select(Category).where(func.lower(Category.name) == category_name.lower())).scalar_one_or_none()
    if not cat:
        cat = Category(name=category_name)
        db.add(cat)
        db.flush()
    return cat.id


def _create_item_from_result(db: Session, result: ProductResult, scanned_barcode: str | None = None) -> Item:
    """Auto-create an Item from an external lookup result.

    Stores the barcode normalised rather than however the provider spelled it,
    and treats a unique-constraint collision as "someone already added this"
    instead of a 500 — two tablets scanning the same pack must not error.
    """
    category_id = _resolve_or_create_category(db, result.category)

    item = Item(
        name=result.name,
        brand=result.brand,
        barcode=normalize_barcode(scanned_barcode or result.barcode or ""),
        barcode_type="UPC",
        image_url=result.image_url,
        size_description=result.size_description,
        size_quantity=result.size_quantity,
        size_unit=result.size_unit,
        stock_mode=infer_stock_mode(result.size_unit),
        # Identity key (ADR 0015). Without it a scanned product can never be
        # matched by a recipe line naming the bare ingredient, so every import
        # mints a generic twin beside the real jar on the shelf.
        canonical_ingredient=canonical_ingredient(result.name, result.brand),
        canonical_provenance="generated",
        canonical_version=CANONICAL_VERSION,
        nutrition=({**result.nutrition, "_reference_verification": {"status": "label_exact", "source": result.source, "description": result.name, "why": "Barcode product reference"}} if result.nutrition else None),
        source=result.source,
        category_id=category_id,
        serving_size_g=result.serving_size_g,
        density_g_per_ml=result.density_g_per_ml,
        grams_per_each=result.grams_per_each,
        ingredients_text=result.ingredients_text,
        allergens=result.allergens,
        nutri_score=result.nutri_score,
        nova_group=result.nova_group,
        eco_score=result.eco_score,
        off_last_checked_at=datetime.now(timezone.utc),
        **_structured_macros_from_nutrition(result.nutrition),
    )
    db.add(item)
    try:
        db.commit()
    except IntegrityError:
        # Already in the catalog under an equivalent GTIN width. Return the
        # existing row rather than failing the scan.
        db.rollback()
        existing = resolve_local_item(db, scanned_barcode or result.barcode or "")
        if existing:
            return existing
        raise
    db.refresh(item)
    # No label macros from the provider? Borrow generic ones by name (estimated).
    _apply_estimated_nutrition(db, item)
    return item


def _structured_macros_from_nutrition(nutrition: dict | None) -> dict:
    """Map OpenFoodFacts per-100g nutriment keys onto the structured item columns
    (ADR 0011 D1). Best-effort: only keys present and numeric are copied; the raw
    ``nutrition`` blob is kept alongside as the source-of-record."""
    if not nutrition:
        return {}

    mapping = {
        "energy-kcal_100g": "energy_kcal_100g",
        "proteins_100g": "protein_g_100g",
        "fat_100g": "fat_g_100g",
        "carbohydrates_100g": "carbs_g_100g",
        "fiber_100g": "fiber_g_100g",
        "sugars_100g": "sugars_g_100g",
        "saturated-fat_100g": "saturated_fat_g_100g",
        "sodium_100g": "sodium_g_100g",
    }
    out: dict = {}
    for source_key, column in mapping.items():
        value = nutrition.get(source_key)
        if value is None:
            continue
        try:
            out[column] = Decimal(str(value))
        except (ValueError, ArithmeticError):
            continue
    return out


def _backfill_structured_macros(db: Session, item: Item) -> None:
    """Fill an existing item's empty structured macro columns from its stored
    ``nutrition`` blob (ADR 0011 D1). Lets a re-scan enrich items created before
    the structured columns existed, or found (not created) on scan. Only fills
    columns that are currently null — never overwrites a user-set value — and
    commits only when something actually changed."""
    if item.nutrition is None:
        # A local hit without nutrition may perform a provider lookup next.
        # There is nothing to backfill, so do not lock the row across network I/O.
        return
    db.flush()
    db.refresh(item, with_for_update=True)
    evidence = (item.nutrition or {}).get("_reference_verification")
    if item.nutrition_estimated and (
        not isinstance(evidence, dict) or evidence.get("status") not in {"verified", "human_selected"}
    ):
        # Keep historical data intact, but do not turn unchecked reference JSON
        # into newly authoritative structured nutrition on a local barcode hit.
        return
    derived = _structured_macros_from_nutrition(item.nutrition)
    changed = False
    for column, value in derived.items():
        if getattr(item, column) is None:
            setattr(item, column, value)
            changed = True
    if changed:
        db.add(item)
        db.commit()
        db.refresh(item)


# Item columns an OFF rescan can fill. Used to report what a rescan added.
_ENRICHABLE_COLUMNS = (
    "brand",
    "image_url",
    "category_id",
    "size_quantity",
    "size_unit",
    "size_description",
    "serving_size_g",
    "density_g_per_ml",
    "grams_per_each",
    "ingredients_text",
    "allergens",
    "nutri_score",
    "nova_group",
    "eco_score",
    "energy_kcal_100g",
    "protein_g_100g",
    "fat_g_100g",
    "carbs_g_100g",
    "fiber_g_100g",
    "sugars_g_100g",
    "saturated_fat_g_100g",
    "sodium_g_100g",
)


def rescan_item(db: Session, item_id: UUID) -> dict:
    """Re-query Open Food Facts for an existing item and fill any missing fields.

    User-triggered "refresh from the API": unlike the passive scan-time
    reconcile, this always re-queries (even for already-enriched items) so a
    catalog entry that was missing size/density/macros can be topped up. Never
    overwrites a value already set. Reports which fields it filled.
    """
    item = db.execute(select(Item).where(Item.id == item_id)).scalar_one_or_none()
    if not item:
        raise LookupError("Item not found")

    barcode = (item.barcode or "").strip()
    before = {column: getattr(item, column) for column in _ENRICHABLE_COLUMNS}

    if barcode:
        try:
            # Rescan is user-triggered: merge across all providers for completeness
            # (OFF's nutrition + USDA's size, etc.) rather than first-hit-wins.
            result = lookup_external_merged(normalize_barcode(barcode), **_external_keys(db))
        except ProviderRateLimited:
            # Don't stamp off_last_checked_at or claim "not found" — the data may
            # well exist; we were just throttled. The caller can retry later.
            return {"item_id": item_id, "status": "rate_limited", "filled": [], "estimated": False}
        if result:
            _reconcile_item_from_result(db, item, result, estimate_nutrition=False)
        else:
            item.off_last_checked_at = datetime.now(timezone.utc)
            db.add(item)
            db.commit()

    # USDA-by-name estimate: the only nutrition source for a no-UPC item (e.g.
    # home-grown eggs), and a fallback when a scanned barcode carried no nutrition.
    # Fills generic per-100g macros matched by name, flags them estimated, and
    # records which USDA food it matched so a wrong guess is auditable.
    reference_review: dict = {}
    estimated_source = _apply_estimated_nutrition(db, item, review=reference_review)

    filled = [c for c in _ENRICHABLE_COLUMNS if before[c] is None and getattr(item, c) is not None]
    if filled:
        return {
            "item_id": item_id,
            "status": "updated",
            "filled": filled,
            "estimated": estimated_source is not None,
            "estimated_source": estimated_source,
            "reference_review": reference_review or None,
        }
    if not barcode:
        # Nothing found by name and no UPC to look up.
        return {"item_id": item_id, "status": "no_barcode", "filled": [], "estimated": False, "estimated_source": None, "reference_review": reference_review or None}
    return {
        "item_id": item_id,
        "status": "no_change" if result else "not_found",
        "filled": [],
        "estimated": False,
        "estimated_source": None,
        "reference_review": reference_review or None,
    }


def _nutrition_evidence(food, status: str, *, why: str = "") -> dict:
    return {"status": status, "source": "usda_fdc", "fdc_id": food.fdc_id,
            "description": food.source_description, "why": why}


def _apply_estimated_nutrition(db: Session, item: Item, *, review: dict | None = None) -> str | None:
    """Lookup, verify, then persist. Never write a rejected/unchecked candidate.

    Existing partial label/manual values are authoritative too: an absent energy
    value does not grant permission to mix an unrelated food into their macros.
    """
    if item.nutrition_estimated or _structured_macros_from_nutrition(item.nutrition) or any(
        getattr(item, c) is not None for c in _ENRICHABLE_COLUMNS if c.endswith("_100g")
    ):
        return None
    original_nutrition = item.nutrition
    original_name = item.name
    try:
        estimate = estimate_nutrition_by_name(item.name, usda_api_key=_external_keys(db)["usda_api_key"])
        if not estimate or not estimate.nutrition:
            return None
        if review is not None:
            review.update(_nutrition_evidence(estimate, "unverified"))
        from modules.automation import food_judge
        verdict = food_judge.same_food(db, product=item.name, reference=estimate.source_description or "")
    except Exception:  # noqa: BLE001 — optional reference lookup never breaks intake
        if review is not None:
            review.update(status="unavailable", source="usda_fdc", why="Reference verification unavailable")
        return None
    status = "verified" if verdict.checked and verdict.same_food else "rejected" if verdict.checked else "unverified"
    evidence = _nutrition_evidence(estimate, status, why=verdict.why)
    if review is not None:
        review.update(evidence)
    if status != "verified":
        return None
    # A human/label write during slow lookup wins. Compare-and-set, rather than
    # a refresh followed by an unguarded write, also closes the final race window.
    source = estimate.source_description or "USDA generic match"
    if estimate.fdc_id:
        source = f"{source} · USDA FDC {estimate.fdc_id}"
    snapshot = Item.nutrition == bindparam("nutrition_snapshot", original_nutrition, type_=Item.nutrition.type)
    if original_nutrition is None:
        snapshot = or_(snapshot, Item.nutrition.is_(None))
    conditions = [Item.id == item.id, Item.name == original_name,
                  Item.nutrition_estimated.is_(False), snapshot]
    conditions.extend(getattr(Item, c).is_(None) for c in _ENRICHABLE_COLUMNS if c.endswith("_100g"))
    values = {"nutrition": {**(original_nutrition or {}), **estimate.nutrition,
                            "_reference_verification": evidence},
              "nutrition_estimated": True, "nutrition_source": source,
              **_structured_macros_from_nutrition(estimate.nutrition)}
    result = db.execute(update(Item).where(*conditions).values(**values),
                        execution_options={"synchronize_session": False})
    db.commit()
    db.refresh(item)
    if not result.rowcount:
        if review is not None:
            review.update(status="unverified", why="Item changed during verification; retained current values")
        return None

    return source


def nutrition_candidates(
    db: Session, item_id: UUID, *, query: str | None = None, limit: int = 20
) -> dict:
    """USDA foods to choose from for an item's nutrition (the picker). Defaults to
    searching the item's name, but takes a free ``query`` so the user can refine
    ("egg" → "egg whole raw") when the right food is buried under egg salad / egg
    foo young. Returns ``{"candidates": [...], "throttled": bool}`` — a distinct
    throttled flag so the UI says "try again" instead of a misleading "no matches"."""
    item = db.execute(select(Item).where(Item.id == item_id)).scalar_one_or_none()
    if not item:
        raise LookupError("Item not found")
    term = (query or "").strip() or item.name
    try:
        candidates = search_nutrition_candidates(
            term, usda_api_key=_external_keys(db)["usda_api_key"], limit=max(1, min(limit, 25))
        )
    except ProviderRateLimited:
        return {"candidates": [], "throttled": True}
    return {
        "throttled": False,
        "candidates": [
            {
                "fdc_id": c.fdc_id,
                "description": c.source_description,
                "data_type": c.data_type,
                "calories": c.nutrition.get("energy-kcal_100g"),
                "protein_g": c.nutrition.get("proteins_100g"),
                "fat_g": c.nutrition.get("fat_100g"),
                "carbs_g": c.nutrition.get("carbohydrates_100g"),
            }
            for c in candidates
        ],
    }


def apply_usda_nutrition(db: Session, item_id: UUID, fdc_id: int, *, query: str | None = None) -> Item:
    """Apply a user-chosen USDA food's per-100g macros to an item (the picker's
    'use this'). Resolves the food from the SAME name search that built the picker
    list (matching on FDC id) rather than a second detail fetch — the detail
    endpoint reports some foods' energy under a different code and would fail to
    parse. Unlike the auto-estimate this OVERWRITES the macro columns (the user
    deliberately picked this food) and stamps the source + estimated flag."""
    item = db.execute(select(Item).where(Item.id == item_id)).scalar_one_or_none()
    if not item:
        raise LookupError("Item not found")
    term = (query or "").strip() or item.name
    food = _resolve_usda_food(fdc_id, term, _external_keys(db)["usda_api_key"])
    if not food or not food.nutrition:
        raise LookupError("That USDA food wasn't in the results — search again and pick it")

    # Replacing the reference replaces its entire managed nutrient set. Do not
    # blend absent nutrients from the prior label/reference into the new one.
    managed = {"energy-kcal_100g", "proteins_100g", "fat_100g", "carbohydrates_100g",
               "fiber_100g", "sugars_100g", "saturated-fat_100g", "sodium_100g"}
    merged = {k: v for k, v in (item.nutrition or {}).items() if k not in managed}
    merged.update({k: v for k, v in food.nutrition.items() if v is not None})
    merged["_reference_verification"] = _nutrition_evidence(food, "human_selected", why="Explicit USDA selection")
    item.nutrition = merged
    macros = _structured_macros_from_nutrition(food.nutrition)
    for column in _ENRICHABLE_COLUMNS:
        if column.endswith("_100g"):
            setattr(item, column, macros.get(column))  # explicit whole-reference replacement
    item.nutrition_estimated = True
    source = food.source_description or "USDA generic match"
    if food.fdc_id:
        source = f"{source} · USDA FDC {food.fdc_id}"
    item.nutrition_source = source
    db.add(item)
    db.commit()
    db.refresh(item)
    return item


# How many trailing words a too-specific query may shed before we give up.
_MAX_BROADENING_STEPS = 3
# Below this, a multi-word query is treated as over-constrained and broadened.
_THIN_RESULTS = 3


def search_food_by_name(db: Session, query: str, *, limit: int = 20) -> dict:
    """USDA generic foods matching a free-text query — the picker for logging a
    meal that's neither in the pantry catalog nor has a barcode ("3 hard boiled
    eggs"). Same shape as :func:`nutrition_candidates` but with no existing item
    to search from.

    USDA's keyword search is effectively conjunctive, so the MORE specific the
    query the FEWER results: "pork ribs" returns plenty, "pork ribs bbq baby
    back" returns almost nothing — the opposite of what a user expects, and it
    makes picking an approximate match impossible. When a multi-word query comes
    back thin, drop trailing words (the most specific modifiers) and retry,
    merging results most-specific-first. ``searched_term`` reports the term that
    actually produced the results so the UI can say so rather than silently
    answering a different question.

    Returns ``{"candidates": [...], "throttled": bool, "searched_term": str}``.
    """
    term = (query or "").strip()
    if not term:
        return {"candidates": [], "throttled": False, "searched_term": term}

    api_key = _external_keys(db)["usda_api_key"]
    want = max(1, min(limit, 25))
    words = term.split()

    merged: list = []
    seen: set = set()
    searched_term = term
    try:
        for step in range(min(_MAX_BROADENING_STEPS + 1, len(words))):
            attempt = " ".join(words[: len(words) - step]) if step else term
            if not attempt:
                break
            found = search_nutrition_candidates(attempt, usda_api_key=api_key, limit=want)
            for candidate in found:
                if candidate.fdc_id in seen:
                    continue
                seen.add(candidate.fdc_id)
                merged.append(candidate)
            if merged and step > 0 and searched_term == term:
                # Record the first broader term that actually produced anything.
                searched_term = attempt
            if len(merged) >= min(want, _THIN_RESULTS) or len(words) == 1:
                break
    except ProviderRateLimited:
        return {"candidates": [], "throttled": True, "searched_term": term}

    candidates = merged[:want]
    return {
        "searched_term": searched_term if candidates else term,
        "throttled": False,
        "candidates": [
            {
                "fdc_id": c.fdc_id,
                "description": c.source_description,
                "data_type": c.data_type,
                "calories": c.nutrition.get("energy-kcal_100g"),
                "protein_g": c.nutrition.get("proteins_100g"),
                "fat_g": c.nutrition.get("fat_100g"),
                "carbs_g": c.nutrition.get("carbohydrates_100g"),
            }
            for c in candidates
        ],
    }


def _resolve_usda_food(fdc_id: int, term: str, api_key: str):
    """Resolve a picked USDA food by its id, deterministically.

    Picking a search result used to be resolved by RE-RUNNING the search and
    matching on fdc_id. That makes a user-visible action depend on a second
    round-trip reproducing the first — and USDA's /foods/search is measurably
    flaky: the identical query returns 25 results one moment and 400s (→ zero
    results) the next. The user then gets "that food wasn't in the results" for
    a row they are looking at.

    The by-id detail endpoint asks for exactly one food, so it can't drift with
    ranking or a failed search. It is tried FIRST; the re-search stays only as a
    fallback for the rare food whose detail record won't parse.

    (A previous comment said the detail endpoint couldn't be used because some
    foods report energy under a different nutrient code — that was fixed in
    ``usda_food_by_fdc_id``, which now handles both response schemas. Don't
    revert to search-only on the strength of that stale note.)
    """
    try:
        by_id = usda_food_by_fdc_id(fdc_id, usda_api_key=api_key)
    except ProviderRateLimited as exc:
        raise ValueError("USDA is throttling — try again shortly") from exc
    if by_id is not None and by_id.nutrition:
        return by_id

    try:
        candidates = search_nutrition_candidates(term, usda_api_key=api_key, limit=25)
    except ProviderRateLimited as exc:
        raise ValueError("USDA is throttling — try again shortly") from exc
    return next((c for c in candidates if c.fdc_id == fdc_id), None)


def get_or_create_item_from_food_search(db: Session, *, query: str, fdc_id: int) -> Item:
    """Find-or-create a lightweight, barcode-less catalog Item for a USDA generic
    food, so it can be plated off-pantry without ever being added to inventory.

    Dedupes on ``usda_fdc_id`` — re-picking the same food across meals reuses
    one Item rather than creating a new row every time. Re-searches the SAME
    query to resolve the chosen ``fdc_id``'s NUTRITION (mirrors
    :func:`apply_usda_nutrition` — the detail-by-id endpoint reports some
    foods' energy under a different code and fails to parse), but its per-each
    PORTION weight comes from a separate, narrow by-id call — ``/foods/search``
    results never carry portion data at all (verified against the live API)."""
    api_key = _external_keys(db)["usda_api_key"]
    existing = db.execute(select(Item).where(Item.usda_fdc_id == fdc_id)).scalar_one_or_none()
    if existing and existing.grams_per_each is not None:
        return existing

    if existing:
        # Backfill an item created before per-each portion weights were
        # captured — a single by-id lookup, no re-search needed since this
        # doesn't touch nutrition. Never overwrites anything else on the item.
        grams_per_each = usda_food_portion_grams_per_each(fdc_id, api_key=api_key)
        if grams_per_each is not None:
            existing.grams_per_each = grams_per_each
            db.add(existing)
            db.commit()
            db.refresh(existing)
        return existing

    term = (query or "").strip()
    food = _resolve_usda_food(fdc_id, term, api_key)
    if not food or not food.nutrition:
        raise LookupError("That USDA food wasn't in the results — search again and pick it")

    # USDA descriptions are ALL CAPS; title-case for a readable catalog name.
    name = (food.source_description or term).strip().title()
    source = food.source_description or "USDA generic match"
    # USDA's own reference weight for one discrete unit ("1 large" egg), when
    # it published one — lets "3 each" resolve real macros instead of only
    # working by weight (None for foods with no portion data).
    grams_per_each = usda_food_portion_grams_per_each(fdc_id, api_key=api_key)

    item = Item(
        name=name[:200],
        source="usda_fdc_generic",
        canonical_ingredient=canonical_ingredient(name),
        canonical_provenance="generated",
        canonical_version=CANONICAL_VERSION,
        usda_fdc_id=fdc_id,
        nutrition={**food.nutrition, "_reference_verification": _nutrition_evidence(food, "human_selected", why="Explicit USDA selection")},
        stock_mode="measured",
        base_unit="g",
        nutrition_estimated=True,
        nutrition_source=f"{source} · USDA FDC {fdc_id}",
        grams_per_each=grams_per_each,
        **_structured_macros_from_nutrition(food.nutrition),
    )
    db.add(item)
    try:
        db.commit()
    except IntegrityError:
        # Concurrent pick of the same food (two tablets logging the same meal
        # at once) — return the row the other request just created.
        db.rollback()
        existing = db.execute(select(Item).where(Item.usda_fdc_id == fdc_id)).scalar_one_or_none()
        if existing:
            return existing
        raise
    db.refresh(item)
    return item


def _reconcile_if_unenriched(db: Session, item: Item, normalized_barcode: str) -> None:
    """Re-scanning an item that has never had any nutrition data captured means
    it was likely created manually (or before OFF integration existed). Check
    the external provider once and fill in whatever it has — never touching a
    field the item already has a value for. An item that already carries a
    nutrition blob is considered enriched and is not re-queried."""
    if item.nutrition is not None:
        return
    try:
        result = lookup_external(normalized_barcode, **_external_keys(db))
    except ProviderRateLimited:
        return  # transient; a later scan or manual rescan will enrich it
    if result:
        _reconcile_item_from_result(db, item, result)


def _reconcile_item_from_result(db: Session, item: Item, result: ProductResult, *, estimate_nutrition: bool = True) -> None:
    """Fill gaps on an existing item from a freshly fetched external result.
    Only ever fills a currently-null field — a value the user (or a prior
    scan) already set is never overwritten."""
    # Lookup may have run while another request edited this row. Preserve pending
    # local work, then lock/reload current authority before filling missing data.
    original_revision = item.canonical_revision
    db.flush()
    db.refresh(item, with_for_update=True)
    if item.canonical_revision != original_revision:
        raise IdentityConflict("Identity changed since lookup. Refresh and review again.")

    def fill(column: str, value) -> None:
        if value is not None and getattr(item, column) is None:
            setattr(item, column, value)

    fill("brand", result.brand)
    fill("image_url", result.image_url)

    # A scan can newly reveal the brand; (re)propose the canonical key when we
    # don't yet have one, so identity matching (ADR 0015) picks this item up.
    if item.canonical_ingredient is None and item.canonical_provenance == "generated":
        change_identity(db, item, key=canonical_ingredient(item.name, item.brand),
                        version=CANONICAL_VERSION, provenance="generated")

    if item.category_id is None and result.category:
        item.category_id = _resolve_or_create_category(db, result.category)

    if item.size_quantity is None and result.size_quantity is not None:
        item.size_quantity = result.size_quantity
        item.size_unit = result.size_unit
        item.size_description = result.size_description
        # A backfilled weight/volume size means the item is used by amount →
        # upgrade the default 'counted' to 'measured' (never downgrade a set mode).
        if item.stock_mode == "counted" and infer_stock_mode(result.size_unit) == "measured":
            item.stock_mode = "measured"

    if result.nutrition and not item.nutrition_estimated:
        was_bare = not _structured_macros_from_nutrition(item.nutrition) and not any(getattr(item, c) is not None for c in _ENRICHABLE_COLUMNS if c.endswith("_100g"))
        merged = dict(item.nutrition or {})
        for key, value in result.nutrition.items():
            if merged.get(key) is None and value is not None:
                merged[key] = value
        if was_bare:
            merged["_reference_verification"] = {"status": "label_exact", "source": result.source, "description": result.name, "why": "Barcode product reference"}
        item.nutrition = merged

    if not item.nutrition_estimated:
        for column, value in _structured_macros_from_nutrition(result.nutrition).items():
            fill(column, value)

    fill("serving_size_g", Decimal(str(result.serving_size_g)) if result.serving_size_g is not None else None)
    fill("density_g_per_ml", Decimal(str(result.density_g_per_ml)) if result.density_g_per_ml is not None else None)
    fill("grams_per_each", Decimal(str(result.grams_per_each)) if result.grams_per_each is not None else None)
    fill("ingredients_text", result.ingredients_text)
    fill("allergens", result.allergens)
    fill("nutri_score", result.nutri_score)
    fill("nova_group", result.nova_group)
    fill("eco_score", result.eco_score)

    item.off_last_checked_at = datetime.now(timezone.utc)
    db.add(item)
    db.commit()
    db.refresh(item)
    # Provider had the product but no macros? Fall back to generic estimated ones.
    if estimate_nutrition:
        _apply_estimated_nutrition(db, item)
