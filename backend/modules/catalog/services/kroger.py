"""Kroger Products API provider (ADR 0013, Phase 3b).

Product search over Kroger's catalog — household goods and food that OFF/USDA
don't carry, with real prices when a store location is configured. Uses OAuth
client-credentials (no redirect URI); dormant until client_id/secret are set
(env or the admin Integrations screen). Prices require ``filter.locationId``.
"""

from __future__ import annotations

import base64
import logging
import time

import httpx

import re as _re

from modules.catalog.services.barcode_providers import ProductResult, ProviderRateLimited, _parse_quantity

logger = logging.getLogger(__name__)

_TOKEN_URL = "https://api.kroger.com/v1/connect/oauth2/token"
_PRODUCTS_URL = "https://api.kroger.com/v1/products"
_LOCATIONS_URL = "https://api.kroger.com/v1/locations"
_TIMEOUT = 6.0

# Short-lived token cache, keyed by client_id (tokens last ~30 min). Cleared in
# tests via reset_token_cache().
_token_cache: dict[str, tuple[str, float]] = {}


def reset_token_cache() -> None:
    _token_cache.clear()


def _fetch_token(client_id: str, client_secret: str) -> str:
    now = time.time()
    cached = _token_cache.get(client_id)
    if cached and cached[1] > now + 30:
        return cached[0]

    basic = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()
    resp = httpx.post(
        _TOKEN_URL,
        data={"grant_type": "client_credentials", "scope": "product.compact"},
        headers={"Authorization": f"Basic {basic}", "Content-Type": "application/x-www-form-urlencoded"},
        timeout=_TIMEOUT,
    )
    if resp.status_code == 429:
        raise ProviderRateLimited("kroger-token")
    resp.raise_for_status()
    body = resp.json()
    token = body["access_token"]
    _token_cache[client_id] = (token, now + float(body.get("expires_in", 1800)))
    return token


_IMG_SIZE_PREFERENCE = ("medium", "small", "large", "thumbnail", "xlarge")


def _first_image_url(product: dict) -> str | None:
    """A list-thumbnail URL: prefer the featured front image at a small-ish size."""
    images = sorted(
        product.get("images") or [],
        key=lambda im: (not im.get("featured"), im.get("perspective") != "front"),
    )
    for image in images:
        by_size = {s.get("size"): s.get("url") for s in (image.get("sizes") or []) if s.get("url")}
        for pref in _IMG_SIZE_PREFERENCE:
            if by_size.get(pref):
                return by_size[pref]
        if by_size:
            return next(iter(by_size.values()))
    return None


def _map_product(product: dict) -> dict:
    items = product.get("items") or []
    first = items[0] if items else {}
    price_block = first.get("price") or {}
    regular = price_block.get("regular")
    try:
        price = float(regular) if regular not in (None, 0, "0") else None
    except (TypeError, ValueError):
        price = None
    return {
        "provider": "kroger",
        "vendor": "kroger",
        "vendor_sku": (product.get("productId") or "").strip() or None,
        "name": (product.get("description") or "").strip(),
        "brand": (product.get("brand") or "").strip() or None,
        "upc": (product.get("upc") or "").strip() or None,
        "size": (first.get("size") or "").strip() or None,
        "price": price,
        "image_url": _first_image_url(product),
    }


def search_kroger_offers(
    term: str,
    *,
    limit: int = 10,
    client_id: str,
    client_secret: str,
    location_id: str | None = None,
    token_override: str | None = None,
) -> list[dict]:
    """Search Kroger's catalog. Empty (no HTTP) when dormant. Raises ProviderRateLimited on 429.

    Prices are only returned when ``location_id`` is set (a Kroger store id).
    ``token_override`` (ADR 0018 §3) supplies a broker-minted access token so
    brokered instances search without local credentials."""
    term = (term or "").strip()
    if not term or not (token_override or (client_id and client_secret)):
        return []

    try:
        token = token_override or _fetch_token(client_id, client_secret)
    except ProviderRateLimited:
        raise
    except httpx.HTTPError as exc:
        logger.warning("Kroger token failed: %s", exc)
        return []

    base_params: dict[str, object] = {"filter.term": term, "filter.limit": max(1, min(limit, 30))}
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}

    def _do(params: dict) -> httpx.Response:
        resp = httpx.get(_PRODUCTS_URL, params=params, headers=headers, timeout=_TIMEOUT)
        if resp.status_code == 401:
            # Token went stale — drop it and retry once.
            _token_cache.pop(client_id, None)
            headers["Authorization"] = f"Bearer {_fetch_token(client_id, client_secret)}"
            resp = httpx.get(_PRODUCTS_URL, params=params, headers=headers, timeout=_TIMEOUT)
        return resp

    try:
        params = dict(base_params)
        if location_id:
            params["filter.locationId"] = location_id
        resp = _do(params)
        # A bad/unknown store id 400s the whole query — retry without it so search
        # still works (just without prices) rather than returning nothing.
        if resp.status_code == 400 and location_id:
            logger.warning("Kroger locationId %r rejected; retrying without prices", location_id)
            resp = _do(dict(base_params))
        if resp.status_code == 429:
            raise ProviderRateLimited("kroger")
        resp.raise_for_status()
    except ProviderRateLimited:
        raise
    except httpx.HTTPError as exc:
        logger.warning("Kroger product search failed: %s", exc)
        return []

    offers = [_map_product(p) for p in (resp.json().get("data") or [])[:limit]]
    return [o for o in offers if o["name"]]


def _get_with_token(url: str, params: dict, client_id: str, client_secret: str) -> httpx.Response | None:
    """Authenticated GET with a one-shot token refresh on 401; None on other errors."""
    token = _fetch_token(client_id, client_secret)
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
    resp = httpx.get(url, params=params, headers=headers, timeout=_TIMEOUT)
    if resp.status_code == 401:
        _token_cache.pop(client_id, None)
        headers["Authorization"] = f"Bearer {_fetch_token(client_id, client_secret)}"
        resp = httpx.get(url, params=params, headers=headers, timeout=_TIMEOUT)
    if resp.status_code == 429:
        raise ProviderRateLimited("kroger")
    resp.raise_for_status()
    return resp


# Kroger nutrient code → the OFF-style per-100g key the pipeline reads. Kroger
# reports these per serving, so they're scaled to per-100g using the serving
# size; sodium is mg (→ grams like OFF).
_KROGER_NUTRIENT_MAP = {
    "ENER-": "energy-kcal_100g",
    "PRO-": "proteins_100g",
    "FAT": "fat_100g",
    "CHO-": "carbohydrates_100g",
    "FIBTG": "fiber_100g",
    "SUGAR": "sugars_100g",
    "FASAT": "saturated-fat_100g",
    "NA": "sodium_100g",
}


def _kroger_gtin_match(product: dict, barcode: str) -> bool:
    target = _re.sub(r"\D", "", barcode).lstrip("0")
    for candidate in (product.get("upc"), product.get("productId")):
        if candidate and _re.sub(r"\D", "", candidate).lstrip("0") == target:
            return True
    return False


def _kroger_nutrition(product: dict) -> tuple[dict | None, float | None, str | None]:
    """(nutrition per-100g, serving grams, ingredients) from Kroger's block."""
    blocks = product.get("nutritionInformation") or []
    if not blocks:
        return None, None, None
    block = blocks[0]
    ingredients = (block.get("ingredientStatement") or "").strip() or None

    serving = block.get("servingSize") or {}
    serving_g = None
    if (serving.get("unitOfMeasure") or {}).get("code") == "GRM":
        try:
            serving_g = float(serving.get("quantity"))
        except (TypeError, ValueError):
            serving_g = None

    nutrition: dict = {}
    if serving_g and serving_g > 0:
        factor = 100.0 / serving_g  # per-serving → per-100g
        for n in block.get("nutrients") or []:
            key = _KROGER_NUTRIENT_MAP.get(n.get("code"))
            if not key:
                continue
            try:
                value = float(n.get("quantity")) * factor
            except (TypeError, ValueError):
                continue
            if n.get("code") == "NA":
                value /= 1000.0  # mg → g
            nutrition[key] = round(value, 4)
    return (nutrition or None), serving_g, ingredients


def lookup_kroger_product(barcode: str, *, client_id: str, client_secret: str) -> ProductResult | None:
    """Enrich a barcode from Kroger's catalog — nutrition, ingredients, size, image.

    Fills the OFF/USDA gap for products only Kroger carries. Dormant (None) until
    credentials are set. Raises ProviderRateLimited on 429."""
    barcode = (barcode or "").strip()
    if not barcode or not client_id or not client_secret:
        return None

    try:
        resp = _get_with_token(_PRODUCTS_URL, {"filter.term": barcode, "filter.limit": 10}, client_id, client_secret)
    except ProviderRateLimited:
        raise
    except httpx.HTTPError as exc:
        logger.warning("Kroger enrichment failed: %s", exc)
        return None
    if resp is None:
        return None

    product = next((p for p in (resp.json().get("data") or []) if _kroger_gtin_match(p, barcode)), None)
    if not product:
        return None
    name = (product.get("description") or "").strip()
    if not name:
        return None

    items = product.get("items") or []
    size_qty, size_unit, size_desc = _parse_quantity((items[0].get("size") if items else None))
    nutrition, serving_g, ingredients = _kroger_nutrition(product)

    return ProductResult(
        barcode=(product.get("upc") or product.get("productId") or barcode).strip(),
        name=name[:200],
        brand=(product.get("brand") or "").strip() or None,
        size_description=size_desc,
        size_quantity=size_qty,
        size_unit=size_unit,
        image_url=_first_image_url(product),
        nutrition=nutrition,
        source="kroger",
        serving_size_g=serving_g,
        ingredients_text=ingredients,
    )


def search_kroger_locations(
    zip_code: str, *, limit: int = 8, client_id: str, client_secret: str
) -> list[dict]:
    """Nearby Kroger stores by ZIP → {location_id, name, address}.

    The store id isn't shown on Kroger's site but the Locations API returns it,
    so a user can pick their store instead of hunting for a number. Empty (no
    HTTP) when dormant."""
    zip_code = (zip_code or "").strip()
    if not zip_code or not client_id or not client_secret:
        return []

    params = {"filter.zipCode.near": zip_code, "filter.limit": max(1, min(limit, 25))}
    try:
        resp = _get_with_token(_LOCATIONS_URL, params, client_id, client_secret)
    except ProviderRateLimited:
        raise
    except httpx.HTTPError as exc:
        logger.warning("Kroger locations failed: %s", exc)
        return []
    if resp is None:
        return []

    out: list[dict] = []
    for loc in resp.json().get("data") or []:
        addr = loc.get("address") or {}
        address = ", ".join(
            p for p in (addr.get("addressLine1"), addr.get("city"), addr.get("state"), addr.get("zipCode")) if p
        )
        out.append(
            {
                "location_id": (loc.get("locationId") or "").strip() or None,
                "name": (loc.get("name") or "").strip() or "Kroger store",
                "address": address,
            }
        )
    return [s for s in out if s["location_id"]]
