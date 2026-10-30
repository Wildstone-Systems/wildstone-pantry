"""External barcode lookup providers.

Cascade: local DB → Open Food Facts → (future: Kroger) → (future: Go-UPC) → manual

Each provider returns a normalised dict or None if not found.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal

import httpx

from app.core import settings
from modules.catalog.services import reference_budget

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Normalised product result (provider-agnostic)
# ---------------------------------------------------------------------------

@dataclass
class NutritionEstimate:
    """A by-name USDA nutrition estimate plus the reference it came from, so an
    approximate match (and a *wrong* one — 'sour cream' resolving to 'heavy cream')
    is auditable instead of silent."""

    nutrition: dict
    source_description: str | None = None
    fdc_id: int | None = None
    data_type: str | None = None


@dataclass
class ProductResult:
    """Normalised product data returned by any provider."""

    barcode: str
    name: str
    brand: str | None = None
    category: str | None = None
    size_description: str | None = None
    size_quantity: float | None = None
    size_unit: str | None = None
    image_url: str | None = None
    nutrition: dict | None = None
    source: str = "unknown"
    found: bool = True
    serving_size_g: float | None = None
    ingredients_text: str | None = None
    allergens: list[str] | None = None
    nutri_score: str | None = None
    nova_group: int | None = None
    eco_score: str | None = None
    density_g_per_ml: float | None = None
    # Weight of ONE countable unit, read off a counted serving ("4 COOKIES
    # (33 g)"). Fixes a different unconvertible class from density: goods
    # stocked as `each` that a recipe asks for by weight.
    grams_per_each: float | None = None


# ---------------------------------------------------------------------------
# Category mapping: OFF categories → PantryOS category names
# ---------------------------------------------------------------------------

_OFF_CATEGORY_MAP: dict[str, str] = {
    "beverages": "Beverages",
    "carbonated-drinks": "Beverages",
    "sodas": "Beverages",
    "colas": "Beverages",
    "sweetened-beverages": "Beverages",
    "dairy": "Dairy",
    "milks": "Dairy",
    "cheeses": "Dairy",
    "yogurts": "Dairy",
    "milk": "Dairy",
    "cheese": "Dairy",
    "yogurt": "Dairy",
    "meats": "Meat & Seafood",
    "poultries": "Meat & Seafood",
    "fishes": "Meat & Seafood",
    "seafood": "Meat & Seafood",
    "meat": "Meat & Seafood",
    "poultry": "Meat & Seafood",
    "fish": "Meat & Seafood",
    "fruits": "Produce",
    "vegetables": "Produce",
    "produce": "Produce",
    "fruit": "Produce",
    "vegetable": "Produce",
    "snacks": "Snacks",
    "cookies": "Snacks",
    "chips": "Snacks",
    "candies": "Snacks",
    "bread": "Bakery",
    "baked-goods": "Bakery",
    "bakery": "Bakery",
    "pastries": "Bakery",
    "frozen": "Frozen",
    "frozen-foods": "Frozen",
    "condiments": "Pantry",
    "spices": "Pantry",
    "sauces": "Pantry",
    "pantry": "Pantry",
    "canned-foods": "Pantry",
    "dry-goods": "Pantry",
    "breakfast-cereals": "Pantry",
    "cereals": "Pantry",
    "pasta": "Pantry",
    "rice": "Pantry",
    "cleaning": "Household",
    "laundry": "Household",
    "dishwashing": "Household",
    "paper-goods": "Household",
    "pet-food": "Pet Supplies",
    "pet-care": "Pet Supplies",
    "baby-foods": "Baby",
    "baby-milks": "Baby",
    "baby": "Baby",
    "alcoholic-beverages": "Beverages",
    "wines": "Beverages",
    "beers": "Beverages",
    "spirits": "Beverages",
    "teas": "Beverages",
    "coffees": "Beverages",
    "juices": "Beverages",
    "spreads": "Pantry",
    "oils": "Pantry",
    "vinegars": "Pantry",
    "soups": "Pantry",
    "delicatessen": "Deli",
    "prepared-meals": "Prepared Foods",
    "meals": "Prepared Foods",
}


def _map_category(off_categories: list[str]) -> str | None:
    """Map Open Food Facts category tags to a PantryOS category name."""
    for cat_tag in off_categories:
        # Strip language prefix: "en:beverages" → "beverages"
        raw = cat_tag.split(":")[-1] if ":" in cat_tag else cat_tag
        if raw in _OFF_CATEGORY_MAP:
            return _OFF_CATEGORY_MAP[raw]
    return None


# ---------------------------------------------------------------------------
# Size/quantity parsing from OFF "quantity" field
# ---------------------------------------------------------------------------

import re as _re

_SIZE_RE = _re.compile(
    r"(?P<qty>\d+(?:\.\d+)?)\s*(?P<unit>fl\s*oz|oz|lb|lbs|gal|gallons?|g|kg|mg|ml|cl|dl|l|liters?|litres?|pints?|pt|quarts?|qt|cups?|each|ea|pack|ct|serving|servings)?\b",
    _re.IGNORECASE,
)

_UNIT_NORMALISE: dict[str, str] = {
    "fl oz": "oz",
    "floz": "oz",
    "oz": "oz",
    "lb": "lb",
    "lbs": "lb",
    "g": "g",
    "kg": "kg",
    "ml": "ml",
    "l": "l",
    "liter": "l",
    "liters": "l",
    "litre": "l",
    "litres": "l",
    "gal": "gal",
    "gallon": "gal",
    "gallons": "gal",
    "pint": "pint",
    "pints": "pint",
    "pt": "pint",
    "qt": "qt",
    "quart": "qt",
    "quarts": "qt",
    "cup": "cup",
    "cups": "cup",
    "pack": "ea",
    "ct": "ea",
    "ea": "ea",
    "serving": "ea",
    "servings": "ea",
}

# Volume units → millilitres, for size/serving conversions. "oz" is deliberately
# absent: OFF normalises "fl oz" to "oz", which then collides with weight ounces,
# so we don't treat a bare "oz" as volumetric.
_ML_PER_UNIT: dict[str, float] = {
    "ml": 1.0,
    "l": 1000.0,
    "cup": 236.588,
    "pint": 473.176,
    "qt": 946.353,
    "gal": 3785.41,
}

# Rough densities (g/ml) for common liquids, used only as a FALLBACK when a
# density cannot be derived from the product's own nutrition (see
# _derive_density_from_energy). Keyed by fragments of OFF category tags.
_LIQUID_DENSITY_BY_TAG: tuple[tuple[tuple[str, ...], float], ...] = (
    (("oil", "oils", "olive-oil"), 0.92),
    (("milk", "milks", "dairy", "dairies", "creams"), 1.03),
    (("juice", "juices"), 1.05),
    (("soda", "sodas", "carbonated"), 1.04),
    (("water", "waters"), 1.00),
)
_DEFAULT_LIQUID_DENSITY = 1.00  # watery liquids sit near 1.0 g/ml


# Spoon and cup measures, for reading a serving stated as a volume. Separate
# from _ML_PER_UNIT (package sizes) because labels use the small measures and
# packages do not.
_SERVING_ML_PER_UNIT: dict[str, float] = {
    "ml": 1.0,
    "millilitre": 1.0,
    "milliliter": 1.0,
    "tsp": 4.92892,
    "teaspoon": 4.92892,
    "tbsp": 14.7868,
    "tbs": 14.7868,
    "tablespoon": 14.7868,
    "cup": 236.588,
    "floz": 29.5735,
    "fl oz": 29.5735,
}

# Labels write fractions both ways, and the unicode forms are common on spice
# jars — "¼ tsp (0.6 g)". Missing these silently drops the whole serving.
_UNICODE_FRACTIONS = {
    "\u00bc": "1/4", "\u00bd": "1/2", "\u00be": "3/4",
    "\u2153": "1/3", "\u2154": "2/3", "\u215b": "1/8",
}


def _serving_amount(raw: str) -> float | None:
    """A leading label quantity: '1', '0.25', '1/4', or '1 1/2'."""
    text = raw.strip()
    mixed = _re.fullmatch(r"(\d+)\s+(\d+)/(\d+)", text)
    if mixed:
        whole, num, den = (float(g) for g in mixed.groups())
        return whole + num / den if den else None
    fraction = _re.fullmatch(r"(\d+)/(\d+)", text)
    if fraction:
        num, den = (float(g) for g in fraction.groups())
        return num / den if den else None
    try:
        return float(text)
    except ValueError:
        return None


def _serving_ml(serving_size_str: str | None) -> float | None:
    """Millilitres in an OFF serving string.

    Handles both shapes labels actually use: an explicit millilitre figure
    ('1 cup (240 ml)', '240ml') and a spoon/cup measure with no ml at all
    ('1 tsp (5 g)', '\u00bc tsp (0.6 g)'). The second is the common one on
    spices and is what makes a density derivable for them — paired with the
    grams in the same string, it IS the density, straight off the label, with
    nothing to match and nothing to guess.
    """
    if not serving_size_str:
        return None

    text = serving_size_str
    for glyph, ascii_form in _UNICODE_FRACTIONS.items():
        text = text.replace(glyph, ascii_form)

    # An explicit ml figure wins — it needs no conversion and labels that carry
    # one are stating the serving volume directly.
    explicit = _re.search(r"(?P<qty>\d+(?:\.\d+)?)\s*ml\b", text, _re.IGNORECASE)
    if explicit:
        return float(explicit.group("qty"))

    units = "|".join(_re.escape(u) for u in sorted(_SERVING_ML_PER_UNIT, key=len, reverse=True))
    # Anchored to the START of the label. The serving is always stated first,
    # and searching the whole string picks up volumes that are not the serving
    # at all — "0.5 cube, MAKES 1 CUP PREPARED" would otherwise read as a
    # 237 ml serving of bouillon powder.
    match = _re.match(
        rf"\s*(?P<qty>\d+\s+\d+/\d+|\d+/\d+|\d+(?:\.\d+)?)\s*(?P<unit>{units})\b\.?",
        text,
        _re.IGNORECASE,
    )
    if not match:
        return None
    amount = _serving_amount(match.group("qty"))
    if amount is None or amount <= 0:
        return None
    return amount * _SERVING_ML_PER_UNIT[match.group("unit").lower()]


# Things a label counts rather than measures: "4 COOKIES (33 g)", "2 bars".
# Reading these gives grams-per-each, which is a different unconvertible class
# from density — counted goods, which is most of what is stocked as 'each'.
_COUNT_WORDS = (
    "cookie", "cookies", "piece", "pieces", "cube", "cubes", "bar", "bars",
    "slice", "slices", "packet", "packets", "stick", "sticks", "wrap", "wraps",
    "egg", "eggs", "tablet", "tablets", "capsule", "capsules",
)


def _serving_count(serving_size_str: str | None) -> float | None:
    """How many countable units one serving is, if the label counts them."""
    if not serving_size_str:
        return None
    text = serving_size_str
    for glyph, ascii_form in _UNICODE_FRACTIONS.items():
        text = text.replace(glyph, ascii_form)
    words = "|".join(_COUNT_WORDS)
    # Anchored for the same reason as _serving_ml.
    match = _re.match(
        rf"\s*(?P<qty>\d+\s+\d+/\d+|\d+/\d+|\d+(?:\.\d+)?)\s*(?P<unit>{words})\b",
        text,
        _re.IGNORECASE,
    )
    if not match:
        return None
    amount = _serving_amount(match.group("qty"))
    return amount if amount and amount > 0 else None


def grams_per_each_from_serving(serving_size_str: str | None, grams: float | None) -> float | None:
    """Weight of ONE countable unit, from a counted serving and its grams.

    "4 COOKIES (33 g)" -> 8.25 g each. Bounded: a single edible unit weighing
    over a kilo is a parse error, not a biscuit.
    """
    count = _serving_count(serving_size_str)
    if not count or not grams or grams <= 0:
        return None
    per_each = grams / count
    return round(per_each, 3) if 0.05 <= per_each <= 1000 else None


def _grams_per_serving_from_energy(nutriments: dict | None) -> float | None:
    """Grams in one serving, derived from OFF's per-serving vs per-100g calories.

    grams/serving = (kcal per serving ÷ kcal per 100 g) × 100. Needs no density
    and no explicit weight — just the two energy figures OFF usually carries.
    """
    if not nutriments:
        return None
    per_serving = nutriments.get("energy-kcal_serving")
    per_100g = nutriments.get("energy-kcal_100g")
    try:
        per_serving = float(per_serving)
        per_100g = float(per_100g)
    except (TypeError, ValueError):
        return None
    if per_serving <= 0 or per_100g <= 0:
        return None
    return (per_serving / per_100g) * 100.0


def _derive_density_from_energy(nutriments: dict | None, serving_size_str: str | None) -> float | None:
    """Density (g/ml) = grams-per-serving ÷ millilitres-per-serving.

    This is the accurate path the user described: if OFF gives both the
    per-serving and per-100g calories AND a volumetric serving, the density
    falls straight out, product-specific rather than guessed. Sanity-clamped to
    plausible food densities.
    """
    grams = _grams_per_serving_from_energy(nutriments)
    millilitres = _serving_ml(serving_size_str)
    if not grams or not millilitres:
        return None
    density = grams / millilitres
    return round(density, 4) if 0.2 <= density <= 2.0 else None


def _estimate_liquid_density(categories_tags: list[str], size_unit: str | None) -> float | None:
    """Fallback density for a liquid we couldn't derive: category default, else ~1.0."""
    if size_unit not in _ML_PER_UNIT:
        return None
    for tag in categories_tags:
        raw = tag.split(":")[-1] if ":" in tag else tag
        for fragments, density in _LIQUID_DENSITY_BY_TAG:
            if raw in fragments:
                return density
    return _DEFAULT_LIQUID_DENSITY

# OFF's own ``serving_size`` label string (e.g. "30 g", "1 cup (240ml)") —
# only the grams figure is extracted; non-gram servings are left null rather
# than guessed at.
_SERVING_GRAMS_RE = _re.compile(r"(?P<qty>\d+(?:\.\d+)?)\s*g\b", _re.IGNORECASE)


def _parse_serving_grams(
    serving_size_str: str | None,
    *,
    serving_quantity: object = None,
    density: float | None = None,
    energy_grams: float | None = None,
) -> float | None:
    """Grams in one serving, from the best signal available.

    In order: an explicit grams figure in the label; OFF's numeric
    ``serving_quantity`` (grams); grams derived from OFF energy figures; or a
    volumetric serving converted via density. Liquids (served in ml) used to
    come back null here — that is why milk showed no serving weight.
    """
    if serving_size_str:
        match = _SERVING_GRAMS_RE.search(serving_size_str)
        if match:
            return float(match.group("qty"))

    # OFF serving_quantity is grams when the serving label is not volumetric.
    if _serving_ml(serving_size_str) is None:
        try:
            value = float(serving_quantity)  # type: ignore[arg-type]
            if value > 0:
                return value
        except (TypeError, ValueError):
            pass

    if energy_grams and energy_grams > 0:
        return round(energy_grams, 2)

    millilitres = _serving_ml(serving_size_str)
    if millilitres and density:
        return round(millilitres * density, 2)

    return None


def _parse_quantity(quantity_str: str | None) -> tuple[float | None, str | None, str | None]:
    """Parse an OFF quantity string into (size_quantity, size_unit, description).

    Keeps the human unit a shopper reads on the pack — '1 gallon' → (1, 'gal') —
    rather than normalising to millilitres. Handles worded US sizes ('half
    gallon', a bare 'quart') that carry no leading number.
    """
    if not quantity_str:
        return None, None, None

    text = quantity_str.strip()
    low = text.lower()

    if "half gallon" in low or "1/2 gallon" in low or "½ gallon" in low:
        return 0.5, "gal", text

    # Multipack like "2 x 150 g" or "6x330ml" → total (300 g / 1980 ml).
    multi = _re.match(r"^\s*(\d+(?:\.\d+)?)\s*[x×]\s*(.+)$", text, _re.IGNORECASE)
    if multi:
        count = float(multi.group(1))
        inner_qty, inner_unit, _ = _parse_quantity(multi.group(2))
        if inner_qty is not None:
            return count * inner_qty, inner_unit, text

    match = _SIZE_RE.search(text)
    if match and match.group("qty"):
        qty = float(match.group("qty"))
        raw_unit = (match.group("unit") or "").strip().lower().replace(" ", "")
        # Metric sub-units carry a value conversion, not just a relabel.
        if raw_unit == "cl":
            return qty * 10.0, "ml", text
        if raw_unit == "dl":
            return qty * 100.0, "ml", text
        if raw_unit == "mg":
            return qty / 1000.0, "g", text
        # Unknown/other units leave the unit null (keeping the description)
        # rather than being mislabelled as a count.
        return qty, _UNIT_NORMALISE.get(raw_unit), text

    # A bare unit word with no number ('gallon', 'quart', 'pint') means one of it.
    for word, unit in (("gallon", "gal"), ("quart", "qt"), ("pint", "pint")):
        if _re.search(rf"\b{word}s?\b", low):
            return 1.0, unit, text

    return None, None, text


# Units that denote a real package SIZE (weight/volume), as opposed to a count
# ('ct'/'pack') — used to pull a size out of a product name.
_SIZE_NAME_UNITS = frozenset({"oz", "lb", "g", "kg", "ml", "l", "gal", "pint", "qt", "cup"})


def _size_from_name(name: str | None) -> tuple[float | None, str | None, str | None]:
    """Best-effort package size parsed from a product NAME when the structured
    size field is empty — many USDA/OFF names embed it ('... 1 Gallon', '16 oz
    Cheese'). Takes the LAST weight/volume-qualified number, so '2% Milk 1
    Gallon' → (1, 'gal') rather than (2, ...) from the '2%'. Count units
    ('12 ct') are ignored — they're not a size."""
    best = None
    for match in _SIZE_RE.finditer(name or ""):
        if not match.group("qty"):
            continue
        raw_unit = (match.group("unit") or "").strip().lower().replace(" ", "")
        if _UNIT_NORMALISE.get(raw_unit) in _SIZE_NAME_UNITS:
            best = match
    if best is None:
        return None, None, None
    return _parse_quantity(best.group(0))


# ---------------------------------------------------------------------------
# Open Food Facts provider
# ---------------------------------------------------------------------------

_OFF_API = "https://world.openfoodfacts.org/api/v0/product/{barcode}.json"
_OFF_FIELDS = (
    "code,product_name,brands,categories_tags,quantity,product_quantity,"
    "product_quantity_unit,serving_size,serving_quantity,"
    "image_url,nutriments,allergens_tags,nutrition_grades,nutriscore_grade,"
    "ecoscore_grade,nova_group,ingredients_text"
)
_OFF_TIMEOUT = 5.0  # seconds


class ProviderRateLimited(Exception):
    """Open Food Facts returned 429 and retries were exhausted.

    Distinct from 'not found' so callers can report it honestly (a rescan that
    got rate-limited should not be recorded as 'no data exists')."""


_OFF_MAX_RETRIES = 2
_OFF_MAX_BACKOFF = 4.0  # seconds; cap for a Retry-After we'll honour synchronously


_USER_AGENT = "PantryOS/1.0 (https://github.com/Wildstone-Systems/wildstone-pantry)"


def _provider_get(url: str, *, params: dict, timeout: float, label: str):
    """GET with bounded 429-retry, shared by every HTTP provider.

    Returns the Response, None on a non-429 error, or raises ProviderRateLimited
    when 429s persist. ``label`` (never ``params``) is what gets logged, so an
    api_key in the query string is not leaked."""
    for attempt in range(_OFF_MAX_RETRIES + 1):
        try:
            resp = httpx.get(url, params=params, timeout=reference_budget.transport_timeout(timeout), headers={"User-Agent": _USER_AGENT})
            resp.raise_for_status()
            return resp
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 429:
                if attempt < _OFF_MAX_RETRIES:
                    delay = _retry_after_seconds(exc.response)
                    left = reference_budget.remaining()
                    if left is not None and delay >= left:
                        raise TimeoutError("Inline reference retry budget exhausted") from exc
                    time.sleep(delay)
                    continue
                raise ProviderRateLimited(label) from exc
            logger.warning("%s lookup failed: %s", label, exc)
            return None
        except httpx.HTTPError as exc:
            logger.warning("%s lookup failed: %s", label, exc)
            return None
    return None


def _off_get(barcode: str):
    return _provider_get(
        _OFF_API.format(barcode=barcode),
        params={"fields": _OFF_FIELDS},
        timeout=_OFF_TIMEOUT,
        label=f"OFF:{barcode}",
    )


def _retry_after_seconds(response) -> float:
    raw = response.headers.get("Retry-After")
    if raw:
        try:
            return min(float(raw), _OFF_MAX_BACKOFF)
        except ValueError:
            pass
    return 1.5


def lookup_open_food_facts(barcode: str) -> ProductResult | None:
    """Look up a barcode in Open Food Facts. Returns None if not found.

    Raises ProviderRateLimited when OFF keeps returning 429."""
    resp = _off_get(barcode)
    if resp is None:
        return None

    data = resp.json()
    if data.get("status") != 1:
        return None

    product = data.get("product", {})
    name = product.get("product_name") or product.get("product_name_en") or ""
    if not name.strip():
        return None

    barcode_val = product.get("code", barcode)
    brand = product.get("brands", "")
    brand = brand.split(",")[0].strip() if brand else None
    categories_tags = product.get("categories_tags", [])
    category = _map_category(categories_tags)

    size_qty, size_unit, size_desc = _parse_quantity(product.get("quantity"))
    if size_qty is None:  # fall back to a size embedded in the product name
        size_qty, size_unit, size_desc = _size_from_name(name)
    image_url = product.get("image_url")
    nutriments = product.get("nutriments")

    # Slim down nutriments to what's useful
    nutrition = None
    if nutriments:
        nutrition = {
            k: v
            for k, v in nutriments.items()
            if k
            in (
                "energy-kcal_100g",
                "energy-kcal_serving",
                "fat_100g",
                "saturated-fat_100g",
                "carbohydrates_100g",
                "sugars_100g",
                "fiber_100g",
                "proteins_100g",
                "salt_100g",
                "sodium_100g",
                "sugars_serving",
                "carbohydrates_serving",
                "proteins_serving",
                "fat_serving",
            )
            and v is not None
        }
        if not nutrition:
            nutrition = None

    # Nutri-Score (newer ``nutriscore_grade``, falling back to the legacy
    # ``nutrition_grades`` name), Eco-Score, and NOVA — kept as first-class
    # fields (not smuggled into the nutrition blob) so they land on their own
    # Item columns.
    nutri_score = (product.get("nutriscore_grade") or product.get("nutrition_grades") or "").strip().lower() or None
    eco_score = (product.get("ecoscore_grade") or "").strip().lower() or None
    nova_group = product.get("nova_group")

    ingredients_text = (product.get("ingredients_text") or "").strip() or None

    allergens_tags = product.get("allergens_tags") or []
    allergens = sorted({(tag.split(":")[-1] if ":" in tag else tag) for tag in allergens_tags if tag}) or None

    serving_str = product.get("serving_size")
    energy_grams = _grams_per_serving_from_energy(nutriments)

    # Density: derive from this product's own numbers when possible (accurate),
    # otherwise fall back to a category estimate for liquids.
    density = _derive_density_from_energy(nutriments, serving_str)
    if density is None:
        density = _estimate_liquid_density(categories_tags, size_unit)

    serving_size_g = _parse_serving_grams(
        serving_str,
        serving_quantity=product.get("serving_quantity"),
        density=density,
        energy_grams=energy_grams,
    )

    return ProductResult(
        barcode=barcode_val,
        name=name.strip()[:200],
        brand=brand,
        category=category,
        size_description=size_desc,
        size_quantity=size_qty,
        size_unit=size_unit,
        image_url=image_url,
        nutrition=nutrition,
        source="open_food_facts",
        serving_size_g=serving_size_g,
        ingredients_text=ingredients_text,
        allergens=allergens,
        nutri_score=nutri_score,
        nova_group=nova_group,
        eco_score=eco_score,
        density_g_per_ml=density,
        grams_per_each=grams_per_each_from_serving(serving_str, serving_size_g),
    )


# ---------------------------------------------------------------------------
# Cascade lookup
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# USDA FoodData Central provider (US branded foods; public-domain data)
# ---------------------------------------------------------------------------

_USDA_API = "https://api.nal.usda.gov/fdc/v1/foods/search"
_USDA_TIMEOUT = 6.0

# USDA nutrient number → the OFF-style per-100g key the rest of the pipeline
# reads. Branded-food nutrient amounts are per 100 g. Sodium is the odd one out:
# USDA reports it in mg, OFF/our columns expect grams.
_USDA_NUTRIENT_MAP: dict[str, str] = {
    "208": "energy-kcal_100g",
    "203": "proteins_100g",
    "204": "fat_100g",
    "205": "carbohydrates_100g",
    "269": "sugars_100g",
    "291": "fiber_100g",
    "606": "saturated-fat_100g",
}


def _usda_query_variants(barcode: str) -> list[str]:
    """UPC widths to try against FDC, most-likely first (it stores mixed widths)."""
    digits = _re.sub(r"\D", "", barcode or "")
    if not digits:
        return []
    core = digits.lstrip("0") or "0"
    ordered = [core.zfill(14), core.zfill(12), digits, core.zfill(13)]
    out: list[str] = []
    for v in ordered:
        if v and v not in out:
            out.append(v)
    return out


def _gtin_matches(candidate: str | None, barcode: str) -> bool:
    """True if two codes are the same GTIN ignoring width/leading-zero padding."""
    a = _re.sub(r"\D", "", candidate or "").lstrip("0")
    b = _re.sub(r"\D", "", barcode or "").lstrip("0")
    return bool(a) and a == b


def _usda_nutrition(food: dict) -> dict | None:
    nutrition: dict = {}
    for n in food.get("foodNutrients") or []:
        # The /foods/search response puts the code at top level (nutrientNumber);
        # the /food/{fdcId} detail response nests it under nutrient.number and uses
        # `amount` instead of `value`. Handle both so a picked food actually parses.
        number = str(
            n.get("nutrientNumber") or n.get("number") or (n.get("nutrient") or {}).get("number") or ""
        )
        amount = n.get("value")
        if amount is None:
            amount = n.get("amount")
        if amount is None:
            continue
        key = _USDA_NUTRIENT_MAP.get(number)
        if key:
            nutrition[key] = amount
        elif number == "307":  # sodium, mg → g
            try:
                nutrition["sodium_100g"] = float(amount) / 1000.0
            except (TypeError, ValueError):
                pass
    return nutrition or None



# Portion `modifier` words that describe a SPOON/CUP/WEIGHT amount rather than
# one discrete unit of the food itself ("1 tbsp" of egg isn't "1 each"). USDA
# lists these ahead of the size-adjective portion ("1 large") for some foods,
# so the first portion with a gramWeight isn't safe to take blindly.
_NON_EACH_PORTION_WORDS = frozenset(
    {
        "cup", "cups", "tbsp", "tablespoon", "tablespoons", "tsp", "teaspoon",
        "teaspoons", "oz", "ounce", "ounces", "ml", "milliliter", "milliliters",
        "fl", "g", "gram", "grams", "lb", "pound", "pounds", "slice", "slices",
        "piece", "pieces", "serving", "servings", "quart", "pint", "gallon",
    }
)


def _grams_per_each_from_portions(food: dict) -> Decimal | None:
    """USDA's own reference weight for ONE discrete unit of a generic food ("1
    large" egg = 50 g), when it published one — lets a whole-food search result
    ("hard boiled egg") be plated by count ("3 each") and still resolve real
    macros, not just by weight. Skips spoon/cup/weight-denominated portions
    ("1 tbsp") that USDA sometimes lists before the size-adjective one. Never a
    guess: absent portion data (most prepared/mixed foods don't have one, or
    only have spoon/cup ones), this returns None and the item is
    plate-by-weight only, same as before."""
    portions = food.get("foodPortions") or []
    for portion in portions:
        grams = portion.get("gramWeight")
        amount = portion.get("amount")
        if grams is None:
            continue
        modifier_tokens = set(_re.split(r"[^a-z]+", str(portion.get("modifier") or "").lower()))
        if modifier_tokens & _NON_EACH_PORTION_WORDS:
            continue
        try:
            grams_dec = Decimal(str(grams))
            amount_dec = Decimal(str(amount)) if amount is not None else Decimal("1")
        except (TypeError, ValueError, ArithmeticError):
            continue
        if amount_dec <= 0:
            continue
        return grams_dec / amount_dec
    return None


def usda_food_portion_grams_per_each(fdc_id: int, *, api_key: str | None = None) -> Decimal | None:
    """USDA's per-each portion weight for a food, fetched by id.

    ``/foods/search`` results never carry ``foodPortions`` (verified against
    the live API — only the ``/food/{fdcId}`` detail endpoint does), so this
    is a second, deliberately narrow call made only when a food is actually
    picked (or backfilled) — it reads nothing but portions, sidestepping the
    detail endpoint's known nutrient-parsing quirk (see
    :func:`search_nutrition_candidates`'s docstring) since nutrition is never
    touched here. Best-effort: None on any failure or missing data."""
    key = (api_key if api_key is not None else settings.usda_fdc_api_key).strip()
    if not key or not fdc_id:
        return None
    resp = _provider_get(
        f"https://api.nal.usda.gov/fdc/v1/food/{int(fdc_id)}",
        params={"api_key": key},
        timeout=_USDA_TIMEOUT,
        label=f"USDA-portions:{fdc_id}",
    )
    if resp is None:
        return None
    return _grams_per_each_from_portions(resp.json())


def lookup_usda_fdc(barcode: str, *, api_key: str | None = None) -> ProductResult | None:
    """Look up a UPC in USDA FoodData Central's Branded Foods dataset.

    Dormant until ``USDA_FDC_API_KEY`` is set (a free key from api.data.gov).
    FDC has no barcode endpoint, so we search the UPC as a term and keep only a
    food whose ``gtinUpc`` actually matches — never a fuzzy text hit. Raises
    ProviderRateLimited on persistent 429."""
    key = (api_key if api_key is not None else settings.usda_fdc_api_key).strip()
    if not key:
        return None

    # FDC matches the query against the gtin as an exact string, and stores gtins
    # at inconsistent widths (often GTIN-14 zero-padded, sometimes UPC-A 12). A
    # zero-stripped query does NOT match, so try the likely widths until one hits.
    food = None
    for query in _usda_query_variants(barcode):
        resp = _provider_get(
            _USDA_API,
            params={"query": query, "dataType": "Branded", "pageSize": 10, "api_key": key},
            timeout=_USDA_TIMEOUT,
            label=f"USDA:{barcode}",
        )
        if resp is None:
            continue
        foods = resp.json().get("foods") or []
        food = next((f for f in foods if _gtin_matches(f.get("gtinUpc"), barcode)), None)
        if food:
            break
    if not food:
        return None

    name = (food.get("description") or "").strip()
    if not name:
        return None
    # USDA descriptions are ALL CAPS; title-case for a readable catalog name.
    name = name.title()

    brand = (food.get("brandName") or food.get("brandOwner") or "").strip() or None
    size_qty, size_unit, size_desc = _parse_quantity(food.get("packageWeight"))
    if size_qty is None:  # USDA often puts the size only in the name ("... 1 Gallon")
        size_qty, size_unit, size_desc = _size_from_name(name)
    nutrition = _usda_nutrition(food)

    serving_str = None
    ss, su = food.get("servingSize"), (food.get("servingSizeUnit") or "").lower()
    if ss is not None and su:
        serving_str = f"{ss} {su}"

    energy_grams = _grams_per_serving_from_energy(nutrition)
    density = _derive_density_from_energy(nutrition, serving_str) or _estimate_liquid_density([], size_unit)
    serving_size_g = _parse_serving_grams(serving_str, density=density, energy_grams=energy_grams)
    if serving_size_g is None and su in ("g", "gram", "grams") and ss is not None:
        try:
            serving_size_g = float(ss)
        except (TypeError, ValueError):
            serving_size_g = None

    return ProductResult(
        barcode=(food.get("gtinUpc") or barcode).strip(),
        name=name[:200],
        brand=brand,
        size_description=size_desc,
        size_quantity=size_qty,
        size_unit=size_unit,
        nutrition=nutrition,
        source="usda_fdc",
        serving_size_g=serving_size_g,
        ingredients_text=(food.get("ingredients") or "").strip() or None,
        density_g_per_ml=density,
    )


def search_usda_products(term: str, *, limit: int = 10, api_key: str | None = None) -> list[dict]:
    """Free-text search of USDA Branded Foods → lightweight product dicts.

    Powers shopping-list offer search for food needs (ADR 0013). Dormant (empty)
    until USDA_FDC_API_KEY is set. Raises ProviderRateLimited on persistent 429."""
    key = (api_key if api_key is not None else settings.usda_fdc_api_key).strip()
    term = (term or "").strip()
    if not key or not term:
        return []

    resp = _provider_get(
        _USDA_API,
        params={"query": term, "dataType": "Branded", "pageSize": max(1, min(limit, 25)), "api_key": key},
        timeout=_USDA_TIMEOUT,
        label=f"USDA-search:{term[:40]}",
    )
    if resp is None:
        return []

    out: list[dict] = []
    for food in (resp.json().get("foods") or [])[:limit]:
        name = (food.get("description") or "").strip()
        if not name:
            continue
        out.append(
            {
                "name": name.title()[:200],
                "brand": (food.get("brandName") or food.get("brandOwner") or "").strip() or None,
                "upc": (food.get("gtinUpc") or "").strip() or None,
                "size": (food.get("packageWeight") or "").strip() or None,
                "source": "usda",
            }
        )
    return out


# USDA's non-branded datasets carry clean per-100g macros for whole foods and
# simple ingredients ("kidney beans", "olive oil", "ground cumin") — the ideal
# source to ESTIMATE macros when no provider has label nutrition for a UPC.
# USDA wants dataType as a comma-joined string, NOT repeated params (which 400).
#
# "Survey (FNDDS)" is deliberately absent. Measured against the live API, the
# parenthesised value makes USDA reject the whole request: 3 of 8 ordinary
# ingredient queries 400'd with it and 0 of 8 without, and dropping it returned
# MORE results (8/8 vs 5/8), not fewer. It was silently costing about a third of
# every by-name nutrition estimate, which is why so many recipe-created items
# ended up with no macros at all.
_USDA_GENERIC_DATATYPES = "Foundation,SR Legacy"

# Brand/marketing words that must not count as a match signal — otherwise a
# store brand ("Smart Way", "Kroger") could borrow an unrelated food's macros.
_ESTIMATE_STOPWORDS = frozenset(
    {
        "the", "and", "with", "for", "original", "brand", "fresh", "natural",
        "organic", "value", "great", "smart", "way", "kroger", "simple", "truth",
        "classic", "premium", "family", "size", "pack", "count",
    }
)


def _name_tokens(text: str | None) -> set[str]:
    """Meaningful (len>=3) lowercase word tokens of a product name."""
    return {t for t in _re.split(r"[^a-z0-9]+", (text or "").lower()) if len(t) >= 3}


def search_nutrition_candidates(
    name: str, *, usda_api_key: str | None = None, limit: int = 3
) -> list[NutritionEstimate]:
    """Top USDA generic-food candidates for a name — the choices behind the
    nutrition picker (ADR follow-up). Same guardrails as the auto-estimate (skip
    Branded, require a shared word, require an energy figure), but returns up to
    ``limit`` distinct foods so the user can pick the right one ("sour cream" vs
    "heavy cream"). De-duplicated by FDC id, ordered as USDA ranked them.

    Empty (never partial-wrong) when dormant/no key or nothing shares a word."""
    key = (usda_api_key if usda_api_key is not None else settings.usda_fdc_api_key).strip()
    term = (name or "").strip()
    if not key or not term:
        return []

    query_tokens = _name_tokens(term) - _ESTIMATE_STOPWORDS
    if not query_tokens:
        return []

    # Fetch a few more than asked so branded/no-energy rows can be filtered out and
    # still leave a full list (the picker asks for ~20 so a plain "Egg, whole" isn't
    # buried under egg salad / egg foo young).
    page_size = min(50, max(25, limit + 20))
    resp = _provider_get(
        _USDA_API,
        params={"query": term, "dataType": _USDA_GENERIC_DATATYPES, "pageSize": page_size, "api_key": key},
        timeout=_USDA_TIMEOUT,
        label=f"USDA-candidates:{term[:40]}",
    )
    if resp is None:
        return []

    candidates: list[NutritionEstimate] = []
    seen_fdc: set = set()
    for food in resp.json().get("foods") or []:
        if food.get("dataType") == "Branded":
            continue  # want generic reference foods, not another branded miss
        nutrition = _usda_nutrition(food)
        if not nutrition or nutrition.get("energy-kcal_100g") is None:
            continue
        # Only trust a match that shares a real word with what we searched for.
        if not (query_tokens & _name_tokens(food.get("description"))):
            continue
        fdc_id = food.get("fdcId")
        if fdc_id in seen_fdc:
            continue
        seen_fdc.add(fdc_id)
        candidates.append(
            NutritionEstimate(
                nutrition=nutrition,
                source_description=(food.get("description") or "").strip() or None,
                fdc_id=fdc_id,
                data_type=food.get("dataType"),
            )
        )
        if len(candidates) >= limit:
            break
    return candidates


def estimate_nutrition_by_name(name: str, *, usda_api_key: str | None = None) -> NutritionEstimate | None:
    """Best-effort GENERIC per-100g macros for a product we couldn't get label
    nutrition for — the single best candidate (the auto-estimate). See
    :func:`search_nutrition_candidates` for the picker that offers the top few."""
    candidates = search_nutrition_candidates(name, usda_api_key=usda_api_key, limit=1)
    return candidates[0] if candidates else None


def usda_food_by_fdc_id(fdc_id: int, *, usda_api_key: str | None = None) -> NutritionEstimate | None:
    """Fetch one exact USDA food by FDC id and parse its per-100g macros — used
    when the user picks a specific candidate, so the applied numbers come straight
    from that record (deterministic, no re-search drift)."""
    key = (usda_api_key if usda_api_key is not None else settings.usda_fdc_api_key).strip()
    if not key or not fdc_id:
        return None
    resp = _provider_get(
        f"https://api.nal.usda.gov/fdc/v1/food/{int(fdc_id)}",
        params={"api_key": key},
        timeout=_USDA_TIMEOUT,
        label=f"USDA-food:{fdc_id}",
    )
    if resp is None:
        return None
    food = resp.json()
    nutrition = _usda_nutrition(food)
    if not nutrition or nutrition.get("energy-kcal_100g") is None:
        return None
    return NutritionEstimate(
        nutrition=nutrition,
        source_description=(food.get("description") or "").strip() or None,
        fdc_id=food.get("fdcId") or int(fdc_id),
        data_type=food.get("dataType"),
    )


def _provider_chain(usda_api_key: str | None, kroger: tuple[str, str] | None):
    """The ordered provider callables: Open Food Facts → USDA → Kroger."""

    def _kroger(bc: str) -> ProductResult | None:
        if not kroger:
            return None
        # Lazy import: kroger imports from this module, so importing at top would
        # be a cycle.
        from modules.catalog.services.kroger import lookup_kroger_product

        return lookup_kroger_product(bc, client_id=kroger[0], client_secret=kroger[1])

    return (
        lookup_open_food_facts,
        lambda bc: lookup_usda_fdc(bc, api_key=usda_api_key),
        _kroger,
    )


def lookup_external(
    barcode: str,
    *,
    usda_api_key: str | None = None,
    kroger: tuple[str, str] | None = None,
) -> ProductResult | None:
    """Try each external provider in order; return the FIRST hit (the fast scan
    path). A provider that's rate-limited is skipped so it can't block the
    others; if every provider missed and at least one was throttled,
    ProviderRateLimited is raised so a rescan reports 'rate_limited' rather than
    'not found'."""
    rate_limited = False
    for provider in _provider_chain(usda_api_key, kroger):
        try:
            result = provider(barcode)
        except ProviderRateLimited:
            rate_limited = True
            continue
        if result:
            return result

    if rate_limited:
        raise ProviderRateLimited(barcode)
    return None


# Fields to borrow from later providers when the primary result lacks them.
_MERGEABLE_FIELDS = (
    "brand",
    "category",
    "image_url",
    "serving_size_g",
    "ingredients_text",
    "allergens",
    "nutri_score",
    "nova_group",
    "eco_score",
    "density_g_per_ml",
)


def _merge_results(primary: ProductResult, others: list[ProductResult]) -> ProductResult:
    """Fill the primary result's gaps from later providers (same product by
    barcode): OFF supplies nutrition/brand, USDA the size in its name, etc."""
    for other in others:
        for field in _MERGEABLE_FIELDS:
            if getattr(primary, field) is None and getattr(other, field) is not None:
                setattr(primary, field, getattr(other, field))
        if primary.size_quantity is None and other.size_quantity is not None:
            primary.size_quantity = other.size_quantity
            primary.size_unit = other.size_unit or primary.size_unit
            primary.size_description = other.size_description or primary.size_description
        if other.nutrition:
            merged = dict(primary.nutrition or {})
            for key, value in other.nutrition.items():
                if merged.get(key) is None and value is not None:
                    merged[key] = value
            primary.nutrition = merged or None
    return primary


def lookup_external_merged(
    barcode: str,
    *,
    usda_api_key: str | None = None,
    kroger: tuple[str, str] | None = None,
) -> ProductResult | None:
    """Like ``lookup_external`` but MERGES data across all providers (fills each
    gap from whichever provider has it) instead of stopping at the first hit.

    Used by the user-triggered rescan, where completeness beats latency: OFF
    often has nutrition but not a package size, while USDA carries the size in
    its product name — merging gives the item both."""
    rate_limited = False
    results: list[ProductResult] = []
    for provider in _provider_chain(usda_api_key, kroger):
        try:
            result = provider(barcode)
        except ProviderRateLimited:
            rate_limited = True
            continue
        if result:
            results.append(result)

    if not results:
        if rate_limited:
            raise ProviderRateLimited(barcode)
        return None
    return _merge_results(results[0], results[1:])

def usda_food_portions(fdc_id: int, *, api_key: str | None = None) -> tuple[list[dict], str] | None:
    """A food's raw ``foodPortions`` plus its description, fetched by id.

    Sibling of :func:`usda_food_portion_grams_per_each`, which answers the
    narrower "what does one of these weigh". This returns the whole portion
    table so a caller can derive a volume↔weight density from the spoon and cup
    rows — the figure that decides whether "1 tsp of coriander" is convertible
    at all. As there, only ``/food/{fdcId}`` carries portions; ``/foods/search``
    never does. Best-effort: None on any failure.
    """
    key = (api_key if api_key is not None else settings.usda_fdc_api_key).strip()
    if not key or not fdc_id:
        return None
    resp = _provider_get(
        f"https://api.nal.usda.gov/fdc/v1/food/{int(fdc_id)}",
        params={"api_key": key},
        timeout=_USDA_TIMEOUT,
        label=f"USDA-portions:{fdc_id}",
    )
    if resp is None:
        return None
    try:
        payload = resp.json()
    except ValueError:
        return None
    return (payload.get("foodPortions") or [], payload.get("description") or "")


def search_usda_generic(term: str, *, limit: int = 3, api_key: str | None = None) -> list[dict]:
    """Generic (non-Branded) USDA foods matching a term, id + description only.

    Branded entries are skipped deliberately: a specific manufacturer's product
    rarely publishes portion data, while the generic reference foods
    ("Spices, pepper, black") almost always do.
    """
    key = (api_key if api_key is not None else settings.usda_fdc_api_key).strip()
    query = (term or "").strip()
    if not key or not query:
        return []
    resp = _provider_get(
        _USDA_API,
        params={
            "api_key": key,
            "query": query,
            "pageSize": max(limit * 3, 10),
            # Deliberately NOT including "Survey (FNDDS)": the parenthesised
            # value makes USDA reject the whole request with a 400, reliably.
            # SR Legacy and Foundation are the reference datasets that actually
            # publish foodPortions anyway.
            "dataType": "SR Legacy,Foundation",
        },
        timeout=_USDA_TIMEOUT,
        label=f"USDA-generic-search:{query[:40]}",
    )
    if resp is None:
        return []
    try:
        foods = resp.json().get("foods") or []
    except ValueError:
        return []
    return [
        {"fdcId": f.get("fdcId"), "description": f.get("description") or ""}
        for f in foods[:limit]
        if f.get("fdcId")
    ]
