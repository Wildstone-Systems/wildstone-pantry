"""Unit conversion to grams — the foundation recipe macros build on (ADR 0011 D2).

Pure and DB-free: convert a ``(quantity, unit)`` amount into **grams** given an
item's density / grams-per-each hints. Three unit classes:

- **Mass** (``g, kg, oz, lb``) — fixed factors.
- **Volume** (``ml, l, cup, tbsp, tsp``) — to ml by fixed factors, then to grams
  via ``density_g_per_ml``.
- **Count** (``each``) — via ``grams_per_each``.

The cardinal rule: **never guess.** When the hint needed for a conversion is
missing (density for volume, grams-per-each for count) or the unit is unknown,
return ``None`` so callers mark a rollup *partial* rather than emit a wrong
number. The DB-touching hint resolution (per-item override → seeded
``ingredient_reference`` → None) lives in ``ingredient_reference.resolve_hints``;
this module only does arithmetic.
"""

from __future__ import annotations

from decimal import Decimal
from difflib import get_close_matches

# Grams per 1 unit of mass.
_MASS_TO_G: dict[str, Decimal] = {
    "g": Decimal("1"),
    "kg": Decimal("1000"),
    "oz": Decimal("28.349523125"),
    "lb": Decimal("453.59237"),
}

# Millilitres per 1 unit of volume (US customary).
_VOLUME_TO_ML: dict[str, Decimal] = {
    "ml": Decimal("1"),
    "l": Decimal("1000"),
    "tsp": Decimal("4.92892"),
    "tbsp": Decimal("14.7868"),
    "floz": Decimal("29.5735"),
    "cup": Decimal("236.588"),
    "pint": Decimal("473.176"),
    "qt": Decimal("946.353"),
    "gal": Decimal("3785.41"),
}

# Common spelling aliases → canonical unit token.
_ALIASES: dict[str, str] = {
    "gram": "g",
    "grams": "g",
    "gramme": "g",
    "grammes": "g",
    "kilogram": "kg",
    "kilograms": "kg",
    "kilo": "kg",
    "kilos": "kg",
    "ounce": "oz",
    "ounces": "oz",
    "pound": "lb",
    "pounds": "lb",
    "lbs": "lb",
    "milliliter": "ml",
    "millilitre": "ml",
    "milliliters": "ml",
    "millilitres": "ml",
    "liter": "l",
    "litre": "l",
    "liters": "l",
    "litres": "l",
    "cups": "cup",
    "tablespoon": "tbsp",
    "tablespoons": "tbsp",
    "tablespoonful": "tbsp",
    "tbsps": "tbsp",
    # Unambiguous tablespoon contractions seen in the wild (TheMealDB and friends).
    # `T` is deliberately absent — see the `t`/`T` note below.
    "tbs": "tbsp",
    "tbl": "tbsp",
    "tbls": "tbsp",
    "tblsp": "tbsp",
    "tblspn": "tbsp",
    "teaspoon": "tsp",
    "teaspoons": "tsp",
    "teaspoonful": "tsp",
    "tsps": "tsp",
    "tspn": "tsp",
    "ea": "each",
    "unit": "each",
    "units": "each",
    "count": "each",
    "pt": "pint",
    "pints": "pint",
    "quart": "qt",
    "quarts": "qt",
    "gallon": "gal",
    "gallons": "gal",
    "fl oz": "floz",
    "floz": "floz",
    "fluid ounce": "floz",
    "fluid ounces": "floz",
}


def normalize_unit(unit: str) -> str:
    """Lower/strip a unit string and resolve common aliases to a canonical token.

    Abbreviation periods are dropped and internal whitespace collapsed first, so
    ``"Tbsp."``, ``"fl. oz."`` and ``"  LBS  "`` all land on their canonical token
    without needing an alias entry apiece.

    Deliberately *not* aliased: bare ``t`` and ``T``. Case is folded before the
    lookup, so the two collapse into one token — and they mean teaspoon and
    tablespoon respectively, a 3× difference. ADR 0017 §3 disallows both as inputs
    rather than pick a side; they fall through as unknown and get flagged.
    """
    token = (unit or "").replace(".", " ").strip().lower()
    token = " ".join(token.split())
    return _ALIASES.get(token, token)


# Package units that imply a MEASURED item (used by weight/volume) rather than a
# counted one (each/pack/ct). Starting default only — always user-overridable.
_MEASURED_UNITS: set[str] = set(_MASS_TO_G) | set(_VOLUME_TO_ML) | {"mg"}


def infer_stock_mode(size_unit: str | None) -> str:
    """Guess counted vs measured from the package unit (ADR 0014): weight/volume →
    'measured'; each/pack/count/unknown → 'counted'. A default the user can change."""
    return "measured" if normalize_unit(size_unit or "") in _MEASURED_UNITS else "counted"


# Units that denote whole, discrete things (a pouch, a can), as opposed to an
# amount (grams, ml). Kept here so intake and shopping agree on what "counted" means.
_COUNT_UNITS: set[str] = {"each", "ea", "ct", "count", "pack", "pk", "unit", "piece", "pieces", ""}


def is_count_unit(unit: str | None) -> bool:
    token = (unit or "").strip().lower()
    return normalize_unit(token) in _COUNT_UNITS or token in _COUNT_UNITS


# The closed set of units a recipe line may use (ADR 0017 §3). Every entry is one
# the conversion engine recognises, so anything selectable is convertible (given a
# density where dimensions cross). Single source of truth for the recipe editor's
# unit picker (served to the frontend) and for server-side validation. `oz` is
# weight; fluid ounces are the distinct `floz` — they must never collapse together.
RECIPE_UNIT_VOCAB: list[dict[str, str]] = [
    {"value": "each", "label": "each", "dimension": "count"},
    {"value": "g", "label": "g", "dimension": "mass"},
    {"value": "kg", "label": "kg", "dimension": "mass"},
    {"value": "oz", "label": "oz (weight)", "dimension": "mass"},
    {"value": "lb", "label": "lb", "dimension": "mass"},
    {"value": "ml", "label": "ml", "dimension": "volume"},
    {"value": "l", "label": "l", "dimension": "volume"},
    {"value": "tsp", "label": "tsp", "dimension": "volume"},
    {"value": "tbsp", "label": "tbsp", "dimension": "volume"},
    {"value": "floz", "label": "fl oz", "dimension": "volume"},
    {"value": "cup", "label": "cup", "dimension": "volume"},
    {"value": "pint", "label": "pint", "dimension": "volume"},
    {"value": "qt", "label": "qt", "dimension": "volume"},
    {"value": "gal", "label": "gal", "dimension": "volume"},
]
_RECIPE_UNIT_VALUES: set[str] = {u["value"] for u in RECIPE_UNIT_VOCAB}


def is_recipe_unit(unit: str | None) -> bool:
    """True if ``unit`` (after alias-normalising, e.g. cups→cup) is in the recipe
    vocabulary — the check the ingredients API enforces."""
    return normalize_unit(unit or "") in _RECIPE_UNIT_VALUES


def to_recipe_unit(unit: str | None) -> str | None:
    """Normalise ``unit`` to a vocabulary token, or None if unrecognised — the
    importer uses None to FLAG a token ('pinch', 'can') for the user to remap."""
    norm = normalize_unit(unit or "")
    return norm if norm in _RECIPE_UNIT_VALUES else None


# Discrete cooking tokens that name a *thing*, not an amount. They aren't in the
# vocabulary (ADR 0017 §3 keeps it a curated allow-list), but "each" is a structurally
# sound remap, so we can offer it as a suggestion for a human or agent to confirm.
# Deliberately absent: `pinch`, `dash`, `handful`, `splash` — these name an amount we
# genuinely cannot pin down, so they get no suggestion rather than a fabricated one.
_COUNTABLE_REMAP_HINTS: set[str] = {
    "bunch",
    "can",
    "clove",
    "ear",
    "fillet",
    "head",
    "jar",
    "leaf",
    "package",
    "packet",
    "pkg",
    "slice",
    "sprig",
    "stalk",
    "stick",
    "strip",
    "tin",
    # A size word modifying an implicit count ("1 large onion") or a recipe-API
    # quirk for "no real unit, just N of these, scaled to the servings" — both
    # unambiguously mean "each" the same way "can"/"jar" do above.
    "large",
    "medium",
    "small",
    "whole",
    "serving",
}


def suggest_unit(unit: str | None) -> str | None:
    """Best-guess vocabulary unit for an unrecognised token, or None.

    Advisory only — used to populate the ``flagged_units`` block on import so the
    caller is told *what to do* rather than just that something was wrong. Never
    applied automatically: the honesty principle (ADR 0017) means an unresolved unit
    stays unresolved until a human or the importing agent confirms the remap.
    """
    token = normalize_unit(unit or "")
    if not token or token in _RECIPE_UNIT_VALUES:
        return None
    if token in _COUNTABLE_REMAP_HINTS or token.rstrip("s") in _COUNTABLE_REMAP_HINTS:
        return "each"
    # Typo path: match against everything a user could legitimately have typed.
    candidates = sorted(_RECIPE_UNIT_VALUES | set(_ALIASES))
    close = get_close_matches(token, candidates, n=1, cutoff=0.8)
    return to_recipe_unit(close[0]) if close else None


def unit_dimension(unit: str | None) -> str:
    """'mass' | 'volume' | 'count' for a unit token (ADR 0016)."""
    u = normalize_unit(unit or "")
    if u in _MASS_TO_G or u == "mg":
        return "mass"
    if u in _VOLUME_TO_ML:
        return "volume"
    return "count"


def default_base_unit(stock_mode: str | None, size_unit: str | None) -> str:
    """The base (stored/consumed) unit an item defaults to (ADR 0016). A COUNTED
    item is always 'each' — you track whole units even if it's labelled by weight
    (a 150 g bag of chips is 1 bag). A MEASURED item takes its size's dimension:
    mass → 'g', volume → 'ml' (count/none → 'each')."""
    if (stock_mode or "counted") != "measured":
        return "each"
    dim = unit_dimension(size_unit)
    return {"mass": "g", "volume": "ml", "count": "each"}[dim]


def base_unit_for(base_unit: str | None, stock_mode: str | None, size_unit: str | None) -> str:
    """An item's effective base unit: its explicit override, else the default."""
    token = (base_unit or "").strip()
    return token or default_base_unit(stock_mode, size_unit)


def net_contents_in_base(
    base_unit: str | None,
    stock_mode: str | None,
    size_quantity,
    size_unit: str | None,
    *,
    density_g_per_ml: Decimal | None = None,
    grams_per_each: Decimal | None = None,
) -> Decimal | None:
    """One purchased package expressed in the item's base unit — the ratio that
    bridges buying and consuming (ADR 0016). A 20-ea pack → 20 (each); a 0.5-gal jug
    → 1893 (ml); a 2-oz jar → 56.7 (g). None when there's no size or no bridge."""
    if not size_quantity:
        return None
    base = base_unit_for(base_unit, stock_mode, size_unit)
    try:
        qty = Decimal(str(size_quantity))
    except (TypeError, ValueError):
        return None
    if is_count_unit(size_unit) and is_count_unit(base):
        return qty  # 20 ea → 20 each
    return convert_quantity(
        qty,
        size_unit or base,
        base,
        density_g_per_ml=density_g_per_ml,
        grams_per_each=grams_per_each,
    )


def pack_unit_count(stock_mode: str | None, size_quantity, size_unit: str | None) -> Decimal | None:
    """How many consumable units a *counted multipack* holds (ADR 0014 follow-up):
    a 20-pouch variety pack sized "20 ea" returns 20; a single "1 ea" item or a
    measured item returns None. This is the pouches-per-pack bridge: inventory
    tracks the units (eat 1 → 19) while purchasing stays by the pack."""
    if (stock_mode or "counted") != "counted":
        return None
    if not size_quantity:
        return None
    try:
        qty = Decimal(str(size_quantity))
    except (TypeError, ValueError):
        return None
    if qty <= 1 or not is_count_unit(size_unit):
        return None
    return qty


def to_grams(
    quantity: Decimal,
    unit: str,
    *,
    density_g_per_ml: Decimal | None = None,
    grams_per_each: Decimal | None = None,
    grams_per_serving: Decimal | None = None,
) -> Decimal | None:
    """Convert ``quantity`` of ``unit`` into grams, or ``None`` if not derivable.

    ``None`` is returned (never a guess) when: the unit is unknown; the unit is a
    volume but no ``density_g_per_ml`` was supplied; the unit is ``each`` but no
    ``grams_per_each`` was supplied; or the unit is ``serving`` but no
    ``grams_per_serving`` was supplied.
    """
    token = normalize_unit(unit)
    qty = Decimal(quantity)

    if token in _MASS_TO_G:
        return qty * _MASS_TO_G[token]

    if token in _VOLUME_TO_ML:
        if density_g_per_ml is None:
            return None
        return qty * _VOLUME_TO_ML[token] * Decimal(density_g_per_ml)

    # "slice" is deliberately NOT a global alias for "each" (it stays flagged for
    # review on the recipe-import path — ADR 0017's honesty principle). Here, for
    # meal-logging grams conversion only, it's just another countable unit.
    if token == "each" or token in ("slice", "slices"):
        if grams_per_each is None:
            return None
        return qty * Decimal(grams_per_each)

    if token == "serving":
        if grams_per_serving is None:
            return None
        return qty * Decimal(grams_per_serving)

    return None


def _dimension(token: str) -> str | None:
    if token in _MASS_TO_G:
        return "mass"
    if token in _VOLUME_TO_ML:
        return "volume"
    if token == "each":
        return "count"
    return None


def _bridge(
    base: Decimal,
    from_dim: str,
    to_dim: str,
    *,
    density: Decimal | None,
    grams_per_each: Decimal | None,
    ml_per_each: Decimal | None,
) -> Decimal | None:
    """Cross-dimension conversion of a canonical amount (g / ml / count).

    ``base`` is already in ``from_dim``'s canonical unit; returns it in
    ``to_dim``'s canonical unit, or ``None`` when the needed bridge is missing.
    """
    if from_dim == to_dim:
        return base
    if from_dim == "volume" and to_dim == "mass":
        return base * density if density is not None else None
    if from_dim == "mass" and to_dim == "volume":
        return base / density if density else None
    if from_dim == "count" and to_dim == "mass":
        return base * grams_per_each if grams_per_each is not None else None
    if from_dim == "mass" and to_dim == "count":
        return base / grams_per_each if grams_per_each else None
    if from_dim == "count" and to_dim == "volume":
        if ml_per_each is not None:
            return base * ml_per_each
        if grams_per_each is not None and density:
            return (base * grams_per_each) / density
        return None
    if from_dim == "volume" and to_dim == "count":
        if ml_per_each:
            return base / ml_per_each
        if grams_per_each and density:
            return (base * density) / grams_per_each
        return None
    return None


def convert_quantity(
    quantity: Decimal,
    from_unit: str,
    to_unit: str,
    *,
    density_g_per_ml: Decimal | None = None,
    grams_per_each: Decimal | None = None,
    ml_per_each: Decimal | None = None,
) -> Decimal | None:
    """Convert ``quantity`` from one unit to another, or ``None`` if not derivable.

    Same-unit is an identity (no hints needed). Same-dimension uses fixed
    factors. Cross-dimension needs the relevant bridge — density (mass↔volume)
    and the item's per-each net content (count↔mass/volume) — and returns
    ``None`` (never a guess) when a bridge is missing.
    """
    fu, tu = normalize_unit(from_unit), normalize_unit(to_unit)
    if fu == tu:
        return Decimal(quantity)

    from_dim, to_dim = _dimension(fu), _dimension(tu)
    if from_dim is None or to_dim is None:
        return None

    qty = Decimal(quantity)
    if from_dim == "mass":
        base = qty * _MASS_TO_G[fu]
    elif from_dim == "volume":
        base = qty * _VOLUME_TO_ML[fu]
    else:
        base = qty

    d = Decimal(density_g_per_ml) if density_g_per_ml is not None else None
    gpe = Decimal(grams_per_each) if grams_per_each is not None else None
    mpe = Decimal(ml_per_each) if ml_per_each is not None else None
    converted_base = _bridge(base, from_dim, to_dim, density=d, grams_per_each=gpe, ml_per_each=mpe)
    if converted_base is None:
        return None

    if to_dim == "mass":
        return converted_base / _MASS_TO_G[tu]
    if to_dim == "volume":
        return converted_base / _VOLUME_TO_ML[tu]
    return converted_base


def grams_per_each_from_size(
    size_quantity: Decimal | None,
    size_unit: str | None,
    *,
    density_g_per_ml: Decimal | None = None,
    grams_per_each: Decimal | None = None,
) -> Decimal | None:
    """Net grams in one 'each' of an item — explicit hint, else derived from its package size."""
    if grams_per_each is not None:
        return Decimal(grams_per_each)
    if size_quantity is None or not size_unit:
        return None
    su = normalize_unit(size_unit)
    q = Decimal(size_quantity)
    if su in _MASS_TO_G:
        return q * _MASS_TO_G[su]
    if su in _VOLUME_TO_ML and density_g_per_ml is not None:
        return q * _VOLUME_TO_ML[su] * Decimal(density_g_per_ml)
    return None


def ml_per_each_from_size(
    size_quantity: Decimal | None,
    size_unit: str | None,
    *,
    density_g_per_ml: Decimal | None = None,
    grams_per_each: Decimal | None = None,
) -> Decimal | None:
    """Net millilitres in one 'each' of an item — from a volume package size, else via weight+density."""
    if size_quantity is not None and size_unit:
        su = normalize_unit(size_unit)
        q = Decimal(size_quantity)
        if su in _VOLUME_TO_ML:
            return q * _VOLUME_TO_ML[su]
        if su in _MASS_TO_G and density_g_per_ml:
            return (q * _MASS_TO_G[su]) / Decimal(density_g_per_ml)
    if grams_per_each is not None and density_g_per_ml:
        return Decimal(grams_per_each) / Decimal(density_g_per_ml)
    return None
