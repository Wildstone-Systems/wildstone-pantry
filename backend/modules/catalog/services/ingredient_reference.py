"""Ingredient reference (density / grams-per-each) + hint resolution (ADR 0011 D2).

``resolve_hints`` is the DB-touching bridge between an ``Item`` and the pure
``units.to_grams``: it resolves density / grams-per-each from (1) the item's own
override columns, else (2) a seeded ``ingredient_reference`` row matched by
normalized name (then category), else ``None``. ``macros_for_amount`` then folds a
``(quantity, unit)`` recipe/plate amount into the item's per-100g macros, returning
``None`` when the amount can't be converted so callers mark a rollup partial.
"""

from __future__ import annotations

from decimal import Decimal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import IngredientReference, Item
from modules.catalog.services.units import normalize_unit, to_grams

# Small starting seed — a *starting point*, not authoritative. Density in g/ml,
# grams_per_each for count items. Correctable/extendable via the CRUD API.
SEED_REFERENCE: list[dict] = [
    {"name": "water", "category": "liquid", "density_g_per_ml": "1.0"},
    {"name": "milk", "category": "liquid", "density_g_per_ml": "1.03"},
    {"name": "vegetable oil", "category": "oil", "density_g_per_ml": "0.92"},
    {"name": "olive oil", "category": "oil", "density_g_per_ml": "0.91"},
    {"name": "canola oil", "category": "oil", "density_g_per_ml": "0.92"},
    {"name": "butter", "category": "fat", "density_g_per_ml": "0.96"},
    {"name": "honey", "category": "sweetener", "density_g_per_ml": "1.42"},
    {"name": "maple syrup", "category": "sweetener", "density_g_per_ml": "1.32"},
    {"name": "all purpose flour", "category": "flour", "density_g_per_ml": "0.53"},
    {"name": "bread flour", "category": "flour", "density_g_per_ml": "0.55"},
    {"name": "granulated sugar", "category": "sweetener", "density_g_per_ml": "0.85"},
    {"name": "brown sugar", "category": "sweetener", "density_g_per_ml": "0.90"},
    {"name": "salt", "category": "seasoning", "density_g_per_ml": "1.22"},
    {"name": "rice", "category": "grain", "density_g_per_ml": "0.85"},
    {"name": "egg", "category": "produce", "grams_per_each": "50"},
    {"name": "banana", "category": "produce", "grams_per_each": "120"},
    {"name": "apple", "category": "produce", "grams_per_each": "182"},
    {"name": "onion", "category": "produce", "grams_per_each": "110"},
    {"name": "garlic clove", "category": "produce", "grams_per_each": "3"},
    {"name": "lemon", "category": "produce", "grams_per_each": "58"},
    # Ground spices/seasonings — approximate; correct per item if needed. These make
    # "1 tsp of cumin" convert to grams for recipe cooking (ADR 0016 Phase 2).
    {"name": "cinnamon", "category": "spice", "density_g_per_ml": "0.45"},
    {"name": "ground cinnamon", "category": "spice", "density_g_per_ml": "0.45"},
    {"name": "cumin", "category": "spice", "density_g_per_ml": "0.52"},
    {"name": "ground cumin", "category": "spice", "density_g_per_ml": "0.52"},
    {"name": "paprika", "category": "spice", "density_g_per_ml": "0.46"},
    {"name": "chili powder", "category": "spice", "density_g_per_ml": "0.50"},
    {"name": "black pepper", "category": "spice", "density_g_per_ml": "0.50"},
    {"name": "garlic powder", "category": "spice", "density_g_per_ml": "0.65"},
    {"name": "onion powder", "category": "spice", "density_g_per_ml": "0.55"},
    {"name": "oregano", "category": "spice", "density_g_per_ml": "0.30"},
    {"name": "basil", "category": "spice", "density_g_per_ml": "0.30"},
    {"name": "thyme", "category": "spice", "density_g_per_ml": "0.38"},
    {"name": "ginger", "category": "spice", "density_g_per_ml": "0.53"},
    {"name": "ground ginger", "category": "spice", "density_g_per_ml": "0.53"},
    {"name": "nutmeg", "category": "spice", "density_g_per_ml": "0.52"},
    {"name": "turmeric", "category": "spice", "density_g_per_ml": "0.60"},
    {"name": "coriander", "category": "spice", "density_g_per_ml": "0.45"},
    {"name": "cayenne", "category": "spice", "density_g_per_ml": "0.45"},
    {"name": "curry powder", "category": "spice", "density_g_per_ml": "0.50"},
    {"name": "chili flakes", "category": "spice", "density_g_per_ml": "0.35"},
    {"name": "red pepper flakes", "category": "spice", "density_g_per_ml": "0.35"},
    # Baking staples.
    {"name": "baking soda", "category": "baking", "density_g_per_ml": "2.20"},
    {"name": "baking powder", "category": "baking", "density_g_per_ml": "0.90"},
    {"name": "cornstarch", "category": "baking", "density_g_per_ml": "0.54"},
    {"name": "cocoa powder", "category": "baking", "density_g_per_ml": "0.53"},
    {"name": "powdered sugar", "category": "sweetener", "density_g_per_ml": "0.56"},
    {"name": "vanilla extract", "category": "baking", "density_g_per_ml": "0.88"},
    # Common wet condiments/dairy.
    {"name": "sour cream", "category": "dairy", "density_g_per_ml": "1.0"},
    {"name": "greek yogurt", "category": "dairy", "density_g_per_ml": "1.05"},
    {"name": "yogurt", "category": "dairy", "density_g_per_ml": "1.03"},
    {"name": "heavy cream", "category": "dairy", "density_g_per_ml": "1.0"},
    {"name": "half and half", "category": "dairy", "density_g_per_ml": "1.01"},
    {"name": "mayonnaise", "category": "condiment", "density_g_per_ml": "0.91"},
    {"name": "ketchup", "category": "condiment", "density_g_per_ml": "1.14"},
    {"name": "mustard", "category": "condiment", "density_g_per_ml": "1.0"},
    {"name": "soy sauce", "category": "condiment", "density_g_per_ml": "1.15"},
    {"name": "vinegar", "category": "condiment", "density_g_per_ml": "1.01"},
    {"name": "peanut butter", "category": "fat", "density_g_per_ml": "1.09"},
    # More produce (grams per each).
    {"name": "carrot", "category": "produce", "grams_per_each": "60"},
    {"name": "potato", "category": "produce", "grams_per_each": "170"},
    {"name": "tomato", "category": "produce", "grams_per_each": "120"},
    {"name": "bell pepper", "category": "produce", "grams_per_each": "120"},
    {"name": "avocado", "category": "produce", "grams_per_each": "200"},
    {"name": "lime", "category": "produce", "grams_per_each": "67"},
]


def _norm(text: str | None) -> str | None:
    if text is None:
        return None
    return text.strip().lower() or None


def seed_reference(db: Session) -> int:
    """Insert any missing seed rows (idempotent). Returns rows added."""
    added = 0
    for entry in SEED_REFERENCE:
        name = _norm(entry["name"])
        exists = db.execute(
            select(IngredientReference).where(IngredientReference.name == name)
        ).scalar_one_or_none()
        if exists:
            continue
        db.add(
            IngredientReference(
                name=name,
                category=entry.get("category"),
                density_g_per_ml=(
                    None if entry.get("density_g_per_ml") is None else Decimal(entry["density_g_per_ml"])
                ),
                grams_per_each=(
                    None if entry.get("grams_per_each") is None else Decimal(entry["grams_per_each"])
                ),
            )
        )
        added += 1
    if added:
        db.commit()
    return added


def build_reference_index(db: Session) -> dict[str, dict]:
    """Preload every reference row keyed by normalized name and by category, so a
    hot loop (recipe availability/scoring over many ingredients) resolves hints in
    memory instead of one query per item (ADR 0017 §10). Pass the result as the
    ``index`` arg to :func:`resolve_hints`."""
    rows = db.execute(select(IngredientReference).order_by(IngredientReference.name.asc())).scalars().all()
    by_name: dict[str, IngredientReference] = {}
    by_category: dict[str, IngredientReference] = {}
    for row in rows:
        if row.name and row.name not in by_name:
            by_name[row.name] = row
        if row.category and row.category not in by_category:  # first by name asc
            by_category[row.category] = row
    return {"by_name": by_name, "by_category": by_category}


def _reference_for_item(
    db: Session, item: Item, index: dict[str, dict] | None = None
) -> IngredientReference | None:
    """Match a reference row by normalized item name, falling back to category.

    With an ``index`` (from :func:`build_reference_index`) the match is in-memory;
    otherwise it queries per call."""
    name = _norm(item.name)
    category = item.category.name if item.category is not None else None
    cat = _norm(category)

    if index is not None:
        if name and (row := index["by_name"].get(name)):
            return row
        return index["by_category"].get(cat) if cat else None

    if name:
        row = db.execute(
            select(IngredientReference).where(IngredientReference.name == name)
        ).scalar_one_or_none()
        if row:
            return row
    # Category fallback: a reference row whose category matches the item's category
    # name (coarser; only used when there's no name-level match).
    if cat:
        return db.execute(
            select(IngredientReference).where(IngredientReference.category == cat).order_by(IngredientReference.name.asc())
        ).scalars().first()
    return None


def resolve_hints(
    db: Session, item: Item, *, index: dict[str, dict] | None = None
) -> tuple[Decimal | None, Decimal | None]:
    """Resolve (density_g_per_ml, grams_per_each): item override → reference → None.
    Pass ``index`` to resolve the reference in memory (ADR 0017 §10)."""
    density = item.density_g_per_ml
    grams_each = item.grams_per_each
    if density is not None and grams_each is not None:
        return Decimal(density), Decimal(grams_each)

    ref = _reference_for_item(db, item, index)
    if ref is not None:
        if density is None and ref.density_g_per_ml is not None:
            density = ref.density_g_per_ml
        if grams_each is None and ref.grams_per_each is not None:
            grams_each = ref.grams_per_each

    return (
        None if density is None else Decimal(density),
        None if grams_each is None else Decimal(grams_each),
    )


def macros_for_amount(db: Session, item: Item, quantity: Decimal, unit: str) -> dict | None:
    """Scale ``item``'s per-100g macros to ``quantity`` of ``unit``.

    Returns ``{calories, protein_g, fat_g, carbs_g}`` (each Decimal or None per the
    fields the item has), or ``None`` when the amount can't be converted to grams.
    """
    density, grams_each = resolve_hints(db, item)
    # "1 serving" is meaningless in grams without the item's own serving size —
    # OFF/USDA products commonly carry one, so a scanned snack logged as
    # "1 serving" (the meal-logger's default unit) can resolve real macros.
    grams_per_serving = None if item.serving_size_g is None else Decimal(item.serving_size_g)
    grams = to_grams(
        Decimal(quantity),
        unit,
        density_g_per_ml=density,
        grams_per_each=grams_each,
        grams_per_serving=grams_per_serving,
    )
    if grams is None:
        return None

    factor = grams / Decimal("100")

    def scale(value: Decimal | None) -> Decimal | None:
        if value is None:
            return None
        return (Decimal(value) * factor).quantize(Decimal("0.01"))

    return {
        "grams": grams.quantize(Decimal("0.01")),
        "calories": scale(item.energy_kcal_100g),
        "protein_g": scale(item.protein_g_100g),
        "fat_g": scale(item.fat_g_100g),
        "carbs_g": scale(item.carbs_g_100g),
    }


# Units a meal can plausibly be logged in, most-specific first. Probed against
# the item to see which actually convert — never offered blindly.
_LOGGABLE_UNIT_CANDIDATES = (
    "each", "slice", "serving", "g", "oz", "lb", "kg", "cup", "tbsp", "tsp", "floz", "ml", "l",
)

# When several units work, prefer the one a human would actually say. A protein
# bar is "1 each", not "60 g" — but grams is the universal fallback that always
# works for anything with per-100g nutrition.
_UNIT_PREFERENCE = ("each", "serving", "slice", "cup", "g", "oz", "ml", "tbsp", "tsp", "floz", "lb", "kg", "l")


def loggable_units(db: Session, item: Item) -> dict:
    """Which units this item can actually be logged in, with the macros of ONE
    of each (ADR-less; drives the meal logger's unit picker).

    The meal logger used to offer the same fixed unit list for every item, so you
    could pick "cup" for a protein bar, submit, and only then discover nothing
    could be converted and the entry logged zero calories. Probing here means the
    UI can offer only what resolves — and show the calories before you commit.

    ``units`` are usable; ``unresolvable`` are the ones that don't convert and
    why, so the UI can explain rather than silently hide them.
    """
    density, grams_each = resolve_hints(db, item)
    grams_per_serving = None if item.serving_size_g is None else Decimal(item.serving_size_g)

    usable: list[dict] = []
    unresolvable: list[dict] = []
    for unit in _LOGGABLE_UNIT_CANDIDATES:
        macros = macros_for_amount(db, item, Decimal(1), unit)
        if macros is None:
            unresolvable.append({"unit": unit, "reason": _why_unconvertible(unit, density, grams_each, grams_per_serving)})
            continue
        usable.append(
            {
                "unit": unit,
                "grams": macros["grams"],
                "calories": macros["calories"],
                "protein_g": macros["protein_g"],
                "fat_g": macros["fat_g"],
                "carbs_g": macros["carbs_g"],
            }
        )

    by_unit = {u["unit"]: u for u in usable}
    recommended = next((u for u in _UNIT_PREFERENCE if u in by_unit), None)
    # Calories only resolve when the item also has per-100g energy; say so
    # plainly rather than letting the caller infer it from nulls.
    has_nutrition = any(u["calories"] is not None for u in usable)
    return {
        "item_id": item.id,
        "item_name": item.name,
        "units": usable,
        "unresolvable": unresolvable,
        "recommended_unit": recommended,
        "has_nutrition": has_nutrition,
    }


def _why_unconvertible(unit: str, density, grams_each, grams_per_serving) -> str:
    token = normalize_unit(unit)
    if token in ("each", "slice", "slices") and grams_each is None:
        return "no weight per each — set grams_per_each on the item"
    if token == "serving" and grams_per_serving is None:
        return "no serving size — set serving_size_g on the item"
    if density is None:
        return "no density — set density_g_per_ml to convert a volume to grams"
    return "unit not recognised"


# --- CRUD ------------------------------------------------------------------


def list_reference(db: Session) -> list[IngredientReference]:
    return list(
        db.execute(select(IngredientReference).order_by(IngredientReference.name.asc())).scalars().all()
    )


def create_reference(
    db: Session,
    *,
    name: str,
    category: str | None = None,
    density_g_per_ml: Decimal | None = None,
    grams_per_each: Decimal | None = None,
    notes: str | None = None,
) -> IngredientReference:
    row = IngredientReference(
        name=_norm(name),
        category=category,
        density_g_per_ml=density_g_per_ml,
        grams_per_each=grams_per_each,
        notes=notes,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def get_reference(db: Session, reference_id: UUID) -> IngredientReference | None:
    return db.execute(
        select(IngredientReference).where(IngredientReference.id == reference_id)
    ).scalar_one_or_none()


def update_reference(db: Session, row: IngredientReference, **fields) -> IngredientReference:
    if "name" in fields and fields["name"] is not None:
        fields["name"] = _norm(fields["name"])
    for key, value in fields.items():
        setattr(row, key, value)
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def delete_reference(db: Session, row: IngredientReference) -> None:
    db.delete(row)
    db.commit()
