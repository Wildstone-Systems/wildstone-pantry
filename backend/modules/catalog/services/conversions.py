"""Fill an item's missing volume↔weight conversion from USDA portion data.

The gap this closes: a recipe asks for "1 tsp ground coriander", the pantry
stocks coriander by the gram, and nothing in the system knows what a teaspoon
of coriander weighs — so the row reads ``cant_convert`` and the cook is left to
work it out. One number, ``density_g_per_ml``, resolves every such row for that
item forever.

**Deliberately not asked of a language model.** Measured against the live
endpoint, a local model answered every density plausibly and was 11–57% wrong —
1.0 g/tsp for ground black pepper, where the real figure is 2.3. Worse, that is
the one error class a user cannot catch: needing the conversion is precisely
why they cannot check it. USDA FoodData Central publishes exactly this, per
food, as ``foodPortions``:

    Spices, pepper, black (fdc 170931)
      1 tsp, ground  ->  2.3 g

Deterministic, authoritative, and already integrated. A model belongs on the
question "is this pantry product the same food as that ingredient" — a judgment
where it scores well and a human can verify the answer at a glance. It does not
belong on physical constants.

Nothing is applied automatically. The suggestion carries the food it came from,
so an operator can see that their "Black pepper grinder refill" was matched to
"Spices, pepper, black" and reject it if the match is wrong.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from decimal import Decimal, InvalidOperation
from uuid import UUID

from sqlalchemy.orm import Session

from app.models import Item

# ml per unit, mirroring modules.catalog.services.units._VOLUME_TO_ML. Only the
# spoon/cup measures recipes actually use — those are what go unconvertible.
_ML_PER = {
    "tsp": Decimal("4.92892"),
    "tbsp": Decimal("14.7868"),
    "cup": Decimal("236.588"),
    "fl oz": Decimal("29.5735"),
}

# USDA modifiers are free text ("1.0 tsp, ground"). Match on the leading measure.
_MODIFIER_PREFIX = {
    "tsp": "tsp",
    "teaspoon": "tsp",
    "tbsp": "tbsp",
    "tablespoon": "tbsp",
    "cup": "cup",
    "fl oz": "fl oz",
}

# Food densities live in a narrow band: ~0.25 g/ml for puffed cereal up to ~2.2
# for salt. Anything outside is a parsing accident or a mis-matched food, and is
# dropped rather than shown — a wrong conversion is worse than none, because the
# blank at least tells you to go and look.
MIN_DENSITY = Decimal("0.15")
MAX_DENSITY = Decimal("2.5")

# How many USDA hits to walk before giving up. The right food is often not
# first, but a long walk means many detail calls against a rate-limited,
# measurably flaky API — four is enough to get past the "salted nuts" band
# without turning one suggestion into a dozen requests.
CANDIDATES_TO_TRY = 4


@dataclass
class DensitySuggestion:
    """One derivable density, with the evidence behind it."""

    density_g_per_ml: Decimal
    source_description: str
    fdc_id: int
    # Did a model confirm the USDA food is the same food as the item?
    #   True  — checked and agreed
    #   False — checked and rejected (this candidate was skipped)
    #   None  — nobody asked; a human is still the gate
    verified: bool | None = None
    verified_why: str = ""
    # The portions it was derived from, so the UI can show "1 tsp = 2.3 g"
    # rather than an abstract 0.47 g/ml that means nothing to a cook.
    portions: list[dict] = field(default_factory=list)

    def grams_for(self, unit: str) -> Decimal | None:
        ml = _ML_PER.get(unit)
        return None if ml is None else (ml * self.density_g_per_ml).quantize(Decimal("0.01"))


def _measure_of(portion: dict) -> str | None:
    """Which volume measure a USDA portion describes, if any."""
    text = " ".join(
        str(portion.get(k) or "") for k in ("modifier", "portionDescription")
    ).lower()
    unit_name = ((portion.get("measureUnit") or {}).get("name") or "").lower()
    haystack = f"{text} {unit_name}".strip()
    for prefix, canonical in _MODIFIER_PREFIX.items():
        # Anchored to a word so "cup" doesn't match "cupcake" and "tsp" doesn't
        # match inside a longer token.
        for token in haystack.replace(",", " ").split():
            if token.startswith(prefix):
                return canonical
    return None


def density_from_portions(portions: list[dict], *, description: str, fdc_id: int) -> DensitySuggestion | None:
    """Derive one density from a USDA food's portion table.

    Several portions usually agree (tsp and tbsp of the same spice imply the
    same density); the median of what we can parse is used so one odd row — a
    "1 dash" rounded to 0.1 g, say — can't drag the answer.
    """
    samples: list[tuple[Decimal, dict]] = []
    for portion in portions or []:
        measure = _measure_of(portion)
        if measure is None:
            continue
        try:
            amount = Decimal(str(portion.get("amount") or 0))
            grams = Decimal(str(portion.get("gramWeight") or 0))
        except (InvalidOperation, TypeError):
            continue
        if amount <= 0 or grams <= 0:
            continue
        ml = _ML_PER[measure] * amount
        density = grams / ml
        if MIN_DENSITY <= density <= MAX_DENSITY:
            samples.append(
                (density, {"measure": measure, "amount": float(amount), "grams": float(grams)})
            )

    if not samples:
        return None

    samples.sort(key=lambda pair: pair[0])
    median = samples[len(samples) // 2][0]
    return DensitySuggestion(
        density_g_per_ml=median.quantize(Decimal("0.0001")),
        source_description=description,
        fdc_id=fdc_id,
        portions=[sample[1] for sample in samples],
    )


def suggest_density(db: Session, item_id: UUID, *, query: str | None = None) -> DensitySuggestion | None:
    """Look up a volume↔weight conversion for this item from USDA.

    ``query`` overrides the search term — the item's own name is often a brand
    ("Black pepper grinder refill") that USDA will not match, so the caller can
    pass the plain food instead. That is precisely the hand-off the matching
    step produces, which is why the two compose: establish *what the food is*,
    then look up *what it weighs*.
    """
    from modules.catalog.services.barcode_providers import (
        search_usda_generic,
        usda_food_portions,
    )

    item = db.get(Item, item_id)
    if item is None:
        raise LookupError("Item not found")

    term = (query or item.name or "").strip()
    if not term:
        return None

    from modules.automation import food_judge

    judging = food_judge.available(db)
    first_unverified: DensitySuggestion | None = None

    for food in search_usda_generic(term, limit=CANDIDATES_TO_TRY):
        fetched = usda_food_portions(int(food["fdcId"]))
        if fetched is None:
            continue
        portions, description = fetched
        description = description or food.get("description") or term
        suggestion = density_from_portions(
            portions, description=description, fdc_id=int(food["fdcId"])
        )
        if suggestion is None:
            continue

        if not judging:
            # No model: hand back the first usable candidate, explicitly
            # unverified. Nothing auto-applies, so the human is the gate — the
            # same place this started.
            return suggestion

        # USDA's search ranks by text, not by meaning: querying "salt" returns
        # "Pecans, salted" ahead of "Salt, table". Every candidate therefore has
        # to be checked for being the same FOOD before its numbers are trusted —
        # otherwise a perfectly accurate olive-oil density gets attached to
        # dried mango, which is exactly what happened before this gate existed.
        # Judged against the ITEM, never against the search term. Judging the
        # term looks better on paper — the model is more accurate on clean food
        # names — but it verifies the wrong thing: a bad search term sails
        # through, because "olive oil" genuinely does match "Oil, olive" even
        # when the item on the shelf is dried mango. The item name is the only
        # thing that is actually true about what is in the pantry.
        #
        # The cost is a stricter gate: it also rejects some valid matches
        # ("Minced Garlic" vs "Garlic, raw"), which show up as no suggestion.
        # That is the right direction to fail — a blank sends someone to look,
        # a confident wrong number does not — and the conversion review page
        # exists so a human can supply what the gate refused.
        verdict = food_judge.same_food(db, product=item.name, reference=description)
        if verdict.checked and verdict.same_food:
            return replace(suggestion, verified=True, verified_why=verdict.why)
        if not verdict.checked and first_unverified is None:
            first_unverified = suggestion  # model died mid-run; degrade, don't fail
        # A checked rejection falls through to the next candidate — that IS the
        # retry loop, and it costs one more USDA lookup rather than a wrong number.

    return first_unverified


def apply_density(db: Session, item_id: UUID, density_g_per_ml: Decimal) -> Item:
    """Set an item's density after a human has accepted the suggestion."""
    if not (MIN_DENSITY <= Decimal(density_g_per_ml) <= MAX_DENSITY):
        raise ValueError(
            f"A density of {density_g_per_ml} g/ml isn't plausible for a food "
            f"(expected between {MIN_DENSITY} and {MAX_DENSITY})."
        )
    item = db.get(Item, item_id)
    if item is None:
        raise LookupError("Item not found")
    item.density_g_per_ml = Decimal(density_g_per_ml)
    db.add(item)
    db.commit()
    db.refresh(item)
    return item


def items_needing_conversion(db: Session) -> list[dict]:
    """The review queue: items a recipe genuinely cannot convert for.

    Filtered hard on purpose. 282 catalogue items have no density; only the
    handful that some recipe actually asks for in spoons or cups are *blocking*
    anything, and a queue of 282 is a wall rather than a task list. An item
    qualifies when a recipe measures it by volume, it is stocked by weight or
    count, and no density is on file.

    Per item, not per recipe: filling one in clears it from every recipe that
    mentions it, now and later.
    """
    from sqlalchemy import select

    from app.models import RecipeIngredient

    # The volume units a recipe can use; anything else needs no density.
    volume_units = tuple(_ML_PER) + ("teaspoon", "teaspoons", "tablespoon", "tablespoons", "cups")

    rows = (
        db.execute(
            select(Item, RecipeIngredient.unit)
            .join(RecipeIngredient, RecipeIngredient.item_id == Item.id)
            .where(
                Item.is_active.is_(True),
                Item.density_g_per_ml.is_(None),
                RecipeIngredient.unit.in_(volume_units),
            )
            .order_by(Item.name.asc())
        )
        .all()
    )

    seen: dict = {}
    for item, recipe_unit in rows:
        # A volume recipe unit against a volume base unit converts already.
        if (item.base_unit or "") in _ML_PER or (item.base_unit or "") in ("ml", "l"):
            continue
        entry = seen.setdefault(
            item.id,
            {
                "item_id": item.id,
                "name": item.name,
                "brand": item.brand,
                "base_unit": item.base_unit,
                "stock_mode": item.stock_mode,
                "size_quantity": float(item.size_quantity) if item.size_quantity is not None else None,
                "size_unit": item.size_unit,
                "recipe_units": [],
            },
        )
        if recipe_unit not in entry["recipe_units"]:
            entry["recipe_units"].append(recipe_unit)

    return sorted(seen.values(), key=lambda row: row["name"].lower())
