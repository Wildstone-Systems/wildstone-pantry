"""External recipe search — "find a chili recipe and import it."

Provider cascade, same shape as the barcode lookup cascade
(``modules.catalog.services.barcode_providers``): TheMealDB is free and needs
no key, so it's always active; Spoonacular is dormant until
``SPOONACULAR_API_KEY`` is set, at which point it joins the search (bigger
catalog, and its ingredients already arrive as structured amount+unit rather
than free text).

A search result carries just enough to render a picker (name, image, where it
came from). Importing needs a second call — ``fetch_recipe_detail`` — that
returns the full recipe already shaped as ``RecipeCreate``, so the caller can
hand it straight to the existing ``/api/recipes/validate`` → ``POST
/api/recipes`` path with no separate import machinery to maintain.
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field

import httpx
from bs4 import BeautifulSoup

from app.core import settings
from modules.catalog.services.barcode_providers import ProviderRateLimited
from modules.catalog.services.units import suggest_unit, to_recipe_unit
from modules.planning.schemas.recipe import ExternalSource, RecipeCreate, RecipeIngredientSpec

logger = logging.getLogger(__name__)

_USER_AGENT = "PantryOS/1.0 (https://github.com/Wildstone-Systems/wildstone-pantry)"
_TIMEOUT = 8.0
_MAX_RETRIES = 2

_THEMEALDB_SEARCH = "https://www.themealdb.com/api/json/v1/1/search.php"
_THEMEALDB_LOOKUP = "https://www.themealdb.com/api/json/v1/1/lookup.php"
_SPOONACULAR_SEARCH = "https://api.spoonacular.com/recipes/complexSearch"
_SPOONACULAR_DETAIL = "https://api.spoonacular.com/recipes/{id}/information"


@dataclass(frozen=True)
class RecipeSearchHit:
    source: str
    external_id: str
    name: str
    image_url: str | None
    source_label: str
    # Whatever the search call itself already carries, at no extra request —
    # enough to make a result card mean something before you commit to
    # opening the preview. None where a provider doesn't have it.
    category: str | None = None
    area: str | None = None  # cuisine/region, e.g. "Italian", "Mexican"
    servings: int | None = None
    ready_in_minutes: int | None = None


def _get(url: str, *, params: dict, label: str) -> httpx.Response | None:
    """GET with bounded 429-retry — mirrors barcode_providers' shared helper,
    kept local since providers don't share private helpers across modules."""
    for attempt in range(_MAX_RETRIES + 1):
        try:
            resp = httpx.get(url, params=params, timeout=_TIMEOUT, headers={"User-Agent": _USER_AGENT})
            resp.raise_for_status()
            return resp
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 429:
                if attempt < _MAX_RETRIES:
                    time.sleep(1.0 * (attempt + 1))
                    continue
                raise ProviderRateLimited(label) from exc
            logger.warning("%s failed: %s", label, exc)
            return None
        except httpx.HTTPError as exc:
            logger.warning("%s failed: %s", label, exc)
            return None
    return None


def _resolve_import_unit(raw_unit: str) -> str:
    """Fold a provider's raw unit token into the recipe vocabulary where
    there's a confident remap (exact/aliased match, or a countable-thing hint
    like "can"/"large"/"servings" -> "each" — see suggest_unit). Anything
    genuinely ambiguous ("pinch", "dash") is returned as-is: RecipeIngredientSpec's
    own lenient validator stores it and flags it for review (ADR 0017 §3)
    rather than fabricating a guess. Unlike the agent/OCR import path, a
    *confident* guess here is applied directly rather than merely offered —
    left merely flagged, a freshly search-imported recipe could never be
    re-saved through the editor without a manual per-row fix first."""
    canonical = to_recipe_unit(raw_unit)
    if canonical is not None:
        return canonical
    guess = suggest_unit(raw_unit)
    return guess if guess is not None else raw_unit


_HTML_TAG_RE = re.compile(r"<[a-zA-Z/][^>]*>")


def _clean_instructions(text: str | None) -> str | None:
    """Some providers (Spoonacular's plain ``instructions`` field, occasional
    community-submitted TheMealDB entries) return HTML — ``<ol><li>Step
    one.</li></ol>`` — instead of plain text, which reads as raw markup
    wherever it's displayed. Only pays the parsing cost when the text actually
    looks like HTML, so ordinary plain-text instructions pass through
    untouched.
    """
    if not text or not _HTML_TAG_RE.search(text):
        return text

    soup = BeautifulSoup(text, "html.parser")
    ordered = soup.find("ol")
    if ordered:
        lines = [
            f"{i}. {li.get_text(' ', strip=True)}"
            for i, li in enumerate(ordered.find_all("li", recursive=False), start=1)
            if li.get_text(strip=True)
        ]
        if lines:
            return "\n".join(lines)

    lines = [t.get_text(" ", strip=True) for t in soup.find_all(["li", "p", "br"])]
    lines = [line for line in lines if line]
    if lines:
        return "\n".join(lines)

    # No recognisable block structure — just strip whatever tags exist.
    return soup.get_text("\n", strip=True) or text


# --- TheMealDB ---------------------------------------------------------------

_FRACTION_RE = re.compile(r"^(\d+)\s+(\d+)/(\d+)")
_SIMPLE_FRACTION_RE = re.compile(r"^(\d+)/(\d+)")
_LEADING_NUM_RE = re.compile(r"^(\d+(?:\.\d+)?)")


def _parse_measure(measure: str) -> tuple[float, str]:
    """"1 1/2 cups", "3/4 cup", "2 cloves", "to taste" -> (quantity, unit).

    Only the first word after the number is kept as the unit candidate — the
    unit-folding path (RecipeIngredientSpec) already turns a good guess into a
    vocabulary unit and flags a bad one for review (ADR 0017 §3), so this errs
    toward simple and lets that existing machinery do the judgment call rather
    than trying to out-guess free text here.
    """
    text = (measure or "").strip()
    if not text:
        return 1.0, "each"

    m = _FRACTION_RE.match(text)
    if m:
        whole, num, den = m.groups()
        qty = float(whole) + float(num) / float(den)
        rest = text[m.end() :].strip()
        return qty, (rest.split(None, 1)[0] if rest else "each")

    m = _SIMPLE_FRACTION_RE.match(text)
    if m:
        num, den = m.groups()
        qty = float(num) / float(den)
        rest = text[m.end() :].strip()
        return qty, (rest.split(None, 1)[0] if rest else "each")

    m = _LEADING_NUM_RE.match(text)
    if m:
        qty = float(m.group(1))
        rest = text[m.end() :].strip()
        return qty, (rest.split(None, 1)[0] if rest else "each")

    # No leading number ("to taste", "pinch", "a few") — keep the whole phrase
    # as the unit so it's visible and flaggable rather than silently dropped.
    return 1.0, text


def _themealdb_meal_to_hit(meal: dict) -> RecipeSearchHit:
    return RecipeSearchHit(
        source="themealdb",
        external_id=str(meal["idMeal"]),
        name=meal.get("strMeal") or "Untitled",
        image_url=meal.get("strMealThumb"),
        source_label="TheMealDB",
        category=meal.get("strCategory") or None,
        area=meal.get("strArea") or None,
    )


def _themealdb_meal_to_recipe(meal: dict) -> RecipeCreate:
    ingredients: list[RecipeIngredientSpec] = []
    for i in range(1, 21):
        name = (meal.get(f"strIngredient{i}") or "").strip()
        if not name:
            continue
        qty, unit = _parse_measure(meal.get(f"strMeasure{i}") or "")
        ingredients.append(RecipeIngredientSpec(name=name, quantity=qty, unit=_resolve_import_unit(unit)))

    meal_id = str(meal["idMeal"])
    return RecipeCreate(
        name=meal.get("strMeal") or "Untitled",
        instructions=_clean_instructions(meal.get("strInstructions")),
        servings=4,  # TheMealDB doesn't report servings; a reasonable, editable default
        ingredients=ingredients,
        create_missing_items=True,
        external_source=ExternalSource(
            source="themealdb",
            id=meal_id,
            url=f"https://www.themealdb.com/meal/{meal_id}",
        ),
    )


def search_themealdb(query: str) -> list[RecipeSearchHit]:
    resp = _get(_THEMEALDB_SEARCH, params={"s": query}, label=f"TheMealDB:{query}")
    if resp is None:
        return []
    meals = resp.json().get("meals") or []
    return [_themealdb_meal_to_hit(m) for m in meals]


def _fetch_themealdb_detail(external_id: str) -> tuple[RecipeCreate, str | None] | None:
    resp = _get(_THEMEALDB_LOOKUP, params={"i": external_id}, label=f"TheMealDB:lookup:{external_id}")
    if resp is None:
        return None
    meals = resp.json().get("meals") or []
    if not meals:
        return None
    meal = meals[0]
    return _themealdb_meal_to_recipe(meal), meal.get("strMealThumb")


# --- Spoonacular (dormant until SPOONACULAR_API_KEY is set) ------------------


def _spoonacular_hit(item: dict) -> RecipeSearchHit:
    dish_types = item.get("dishTypes") or []
    cuisines = item.get("cuisines") or []
    return RecipeSearchHit(
        source="spoonacular",
        external_id=str(item["id"]),
        name=item.get("title") or "Untitled",
        image_url=item.get("image"),
        source_label="Spoonacular",
        category=str(dish_types[0]).title() if dish_types else None,
        area=str(cuisines[0]) if cuisines else None,
        servings=item.get("servings"),
        ready_in_minutes=item.get("readyInMinutes"),
    )


def search_spoonacular(query: str, *, api_key: str | None = None) -> list[RecipeSearchHit]:
    key = (api_key if api_key is not None else settings.spoonacular_api_key).strip()
    if not key:
        return []
    resp = _get(
        _SPOONACULAR_SEARCH,
        # addRecipeInformation costs a little more per search but fills in
        # servings/time/cuisine right in the results list — without it every
        # card is just a picture and a name with nothing to go on before
        # opening the preview.
        params={"query": query, "apiKey": key, "number": 10, "addRecipeInformation": "true"},
        label=f"Spoonacular:{query}",
    )
    if resp is None:
        return []
    results = resp.json().get("results") or []
    return [_spoonacular_hit(r) for r in results]


def _spoonacular_ingredient_to_spec(ing: dict) -> RecipeIngredientSpec | None:
    name = (ing.get("nameClean") or ing.get("name") or "").strip()
    if not name:
        return None
    amount = ing.get("amount")
    unit = (ing.get("unit") or "").strip()
    qty = float(amount) if amount else 1.0
    return RecipeIngredientSpec(name=name, quantity=qty, unit=_resolve_import_unit(unit) if unit else "each")


def _fetch_spoonacular_detail(external_id: str, *, api_key: str | None = None) -> RecipeCreate | None:
    key = (api_key if api_key is not None else settings.spoonacular_api_key).strip()
    if not key:
        return None
    resp = _get(
        _SPOONACULAR_DETAIL.format(id=external_id),
        params={"apiKey": key, "includeNutrition": "false"},
        label=f"Spoonacular:detail:{external_id}",
    )
    if resp is None:
        return None
    data = resp.json()
    ingredients = [
        spec
        for spec in (_spoonacular_ingredient_to_spec(i) for i in data.get("extendedIngredients") or [])
        if spec is not None
    ]
    return RecipeCreate(
        name=data.get("title") or "Untitled",
        instructions=_clean_instructions(data.get("instructions")) or None,
        servings=data.get("servings") or 1,
        ingredients=ingredients,
        create_missing_items=True,
        external_source=ExternalSource(
            source="spoonacular",
            id=str(external_id),
            url=data.get("sourceUrl"),
        ),
    )


# --- Cascade -------------------------------------------------------------


def active_providers(*, spoonacular_api_key: str = "") -> list[str]:
    providers = ["themealdb"]
    if spoonacular_api_key.strip():
        providers.append("spoonacular")
    return providers


def search_recipes(query: str, *, spoonacular_api_key: str = "") -> list[RecipeSearchHit]:
    """Search every active provider; a rate-limited or failed provider just
    contributes nothing rather than failing the whole search (same degrade
    used everywhere else providers cascade in this app). ``spoonacular_api_key``
    is the caller's already-resolved effective key (DB override over env — see
    ``app.services.provider_keys.spoonacular_api_key``); this module stays
    DB-agnostic and just takes the string."""
    hits: list[RecipeSearchHit] = []
    try:
        hits.extend(search_themealdb(query))
    except ProviderRateLimited:
        logger.warning("TheMealDB rate-limited for query %r", query)

    if spoonacular_api_key.strip():
        try:
            hits.extend(search_spoonacular(query, api_key=spoonacular_api_key))
        except ProviderRateLimited:
            logger.warning("Spoonacular rate-limited for query %r", query)

    return hits


def fetch_recipe_detail(
    source: str, external_id: str, *, spoonacular_api_key: str = ""
) -> tuple[RecipeCreate, str | None]:
    """Returns (recipe, image_url). Raises LookupError for an unknown source or
    a source with nothing at that id; ValueError for a source that isn't
    configured (Spoonacular with no key)."""
    if source == "themealdb":
        result = _fetch_themealdb_detail(external_id)
        if result is None:
            raise LookupError(f"No TheMealDB recipe at id {external_id}")
        return result

    if source == "spoonacular":
        if not spoonacular_api_key.strip():
            raise ValueError("Spoonacular is not configured — add a key on the Integrations page")
        recipe = _fetch_spoonacular_detail(external_id, api_key=spoonacular_api_key)
        if recipe is None:
            raise LookupError(f"No Spoonacular recipe at id {external_id}")
        return recipe, None

    raise LookupError(f"Unknown recipe source: {source}")
