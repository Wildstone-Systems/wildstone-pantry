"""Kroger product-search provider: OAuth, mapping, prices, dormancy (ADR 0013 3b)."""

from __future__ import annotations

import httpx
import pytest

from modules.catalog.services import kroger
from modules.catalog.services.barcode_providers import ProviderRateLimited


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    kroger.reset_token_cache()
    yield
    kroger.reset_token_cache()


def _token_resp() -> httpx.Response:
    return httpx.Response(
        200, json={"access_token": "tok-123", "expires_in": 1800}, request=httpx.Request("POST", "https://k.test")
    )


_PRODUCTS = {
    "data": [
        {
            "productId": "0001111041700",
            "upc": "0001111041700",
            "brand": "Kroger",
            "description": "Paper Towels, Strong & Absorbent",
            "images": [{"sizes": [{"url": "https://img.test/pt.jpg"}]}],
            "items": [{"size": "6 rolls", "price": {"regular": 7.99, "promo": 6.49}}],
        },
        {"productId": "x", "description": "", "items": []},  # dropped (no name)
    ]
}


def _wire(monkeypatch, products=_PRODUCTS):
    def fake_post(url, **_k):
        return _token_resp()

    def fake_get(url, **_k):
        return httpx.Response(200, json=products, request=httpx.Request("GET", "https://k.test"))

    monkeypatch.setattr(kroger.httpx, "post", fake_post)
    monkeypatch.setattr(kroger.httpx, "get", fake_get)


def test_dormant_without_credentials_makes_no_call(monkeypatch):
    monkeypatch.setattr(kroger.httpx, "post", lambda *a, **k: pytest.fail("should not auth"))
    assert kroger.search_kroger_offers("paper towels", client_id="", client_secret="") == []


def test_maps_products_with_price(monkeypatch):
    _wire(monkeypatch)
    offers = kroger.search_kroger_offers(
        "paper towels", client_id="cid", client_secret="sec", location_id="01400943"
    )
    assert len(offers) == 1  # the empty-name one is dropped
    o = offers[0]
    assert o["name"] == "Paper Towels, Strong & Absorbent"
    assert o["vendor"] == "kroger"
    assert o["vendor_sku"] == "0001111041700"
    assert o["size"] == "6 rolls"
    assert o["price"] == 7.99
    assert o["image_url"] == "https://img.test/pt.jpg"


def test_token_is_cached_across_searches(monkeypatch):
    calls = {"post": 0}

    def fake_post(url, **_k):
        calls["post"] += 1
        return _token_resp()

    monkeypatch.setattr(kroger.httpx, "post", fake_post)
    monkeypatch.setattr(
        kroger.httpx,
        "get",
        lambda *a, **k: httpx.Response(200, json=_PRODUCTS, request=httpx.Request("GET", "https://k.test")),
    )
    kroger.search_kroger_offers("a", client_id="cid", client_secret="sec")
    kroger.search_kroger_offers("b", client_id="cid", client_secret="sec")
    assert calls["post"] == 1  # token reused


def test_bad_location_id_retries_without_prices(monkeypatch):
    """An invalid store id 400s; search retries without it instead of failing."""
    monkeypatch.setattr(kroger.httpx, "post", lambda *a, **k: _token_resp())
    calls = {"n": 0}

    def fake_get(url, **kwargs):
        calls["n"] += 1
        params = kwargs.get("params", {})
        if "filter.locationId" in params:
            return httpx.Response(400, json={}, request=httpx.Request("GET", "https://k.test"))
        return httpx.Response(200, json=_PRODUCTS, request=httpx.Request("GET", "https://k.test"))

    monkeypatch.setattr(kroger.httpx, "get", fake_get)
    offers = kroger.search_kroger_offers("x", client_id="c", client_secret="s", location_id="00125")
    assert len(offers) == 1  # retried without location and got results
    assert calls["n"] == 2


def test_429_raises_rate_limited(monkeypatch):
    monkeypatch.setattr(kroger.httpx, "post", lambda *a, **k: _token_resp())
    monkeypatch.setattr(
        kroger.httpx,
        "get",
        lambda *a, **k: httpx.Response(429, json={}, request=httpx.Request("GET", "https://k.test")),
    )
    with pytest.raises(ProviderRateLimited):
        kroger.search_kroger_offers("x", client_id="cid", client_secret="sec")


_LOCATIONS = {
    "data": [
        {
            "locationId": "01400943",
            "name": "Kroger - Downtown",
            "address": {"addressLine1": "100 Main St", "city": "Cincinnati", "state": "OH", "zipCode": "45202"},
        },
        {"locationId": "", "name": "No id"},  # dropped
    ]
}


def test_locations_search_maps_stores(monkeypatch):
    monkeypatch.setattr(kroger.httpx, "post", lambda *a, **k: _token_resp())
    monkeypatch.setattr(
        kroger.httpx,
        "get",
        lambda *a, **k: httpx.Response(200, json=_LOCATIONS, request=httpx.Request("GET", "https://k.test")),
    )
    stores = kroger.search_kroger_locations("45202", client_id="cid", client_secret="sec")
    assert len(stores) == 1
    assert stores[0]["location_id"] == "01400943"
    assert "Cincinnati" in stores[0]["address"]


def test_locations_dormant_without_creds(monkeypatch):
    monkeypatch.setattr(kroger.httpx, "post", lambda *a, **k: pytest.fail("should not auth"))
    assert kroger.search_kroger_locations("45202", client_id="", client_secret="") == []


_ENRICH_PRODUCT = {
    "data": [
        {
            "productId": "0003000001020",
            "upc": "0003000001020",
            "brand": "Quaker",
            "description": "Quaker Old Fashioned Oats",
            "images": [{"perspective": "front", "featured": True, "sizes": [{"size": "medium", "url": "https://img/oats.jpg"}]}],
            "items": [{"size": "18 oz"}],
            "nutritionInformation": [
                {
                    "ingredientStatement": "Whole Grain Rolled Oats.",
                    "servingSize": {"quantity": 40, "unitOfMeasure": {"code": "GRM"}},
                    "nutrients": [
                        {"code": "ENER-", "quantity": 150},   # per 40g → 375/100g
                        {"code": "PRO-", "quantity": 5},       # → 12.5/100g
                        {"code": "NA", "quantity": 0},         # sodium mg → 0 g
                        {"code": "CA", "quantity": 20},        # calcium — ignored
                    ],
                }
            ],
        }
    ]
}


def test_enrichment_maps_nutrition_per_100g(monkeypatch):
    monkeypatch.setattr(kroger.httpx, "post", lambda *a, **k: _token_resp())
    monkeypatch.setattr(
        kroger.httpx,
        "get",
        lambda *a, **k: httpx.Response(200, json=_ENRICH_PRODUCT, request=httpx.Request("GET", "https://k.test")),
    )
    r = kroger.lookup_kroger_product("0003000001020", client_id="c", client_secret="s")
    assert r is not None
    assert r.source == "kroger"
    assert r.name == "Quaker Old Fashioned Oats"
    assert (r.size_quantity, r.size_unit) == (18.0, "oz")
    assert r.serving_size_g == 40.0
    assert r.ingredients_text == "Whole Grain Rolled Oats."
    assert r.image_url == "https://img/oats.jpg"
    # per-serving (40 g) → per-100g
    assert r.nutrition["energy-kcal_100g"] == pytest.approx(375.0)
    assert r.nutrition["proteins_100g"] == pytest.approx(12.5)
    assert "calcium" not in str(r.nutrition)  # non-macro ignored


def test_enrichment_dormant_without_creds(monkeypatch):
    monkeypatch.setattr(kroger.httpx, "post", lambda *a, **k: pytest.fail("should not auth"))
    assert kroger.lookup_kroger_product("123", client_id="", client_secret="") is None


def test_cascade_uses_kroger_when_off_usda_miss(monkeypatch):
    """Kroger enriches a barcode OFF and USDA don't have."""
    from modules.catalog.services import barcode_providers as bp

    monkeypatch.setattr(bp, "lookup_open_food_facts", lambda bc: None)
    monkeypatch.setattr(bp, "lookup_usda_fdc", lambda bc, api_key=None: None)
    monkeypatch.setattr(kroger.httpx, "post", lambda *a, **k: _token_resp())
    monkeypatch.setattr(
        kroger.httpx,
        "get",
        lambda *a, **k: httpx.Response(200, json=_ENRICH_PRODUCT, request=httpx.Request("GET", "https://k.test")),
    )
    r = bp.lookup_external("0003000001020", kroger=("c", "s"))
    assert r is not None and r.source == "kroger"


def test_offer_cascade_includes_kroger(client: httpx.Client, monkeypatch):
    """With Kroger creds resolved, the shopping search returns Kroger offers."""
    from app.services import provider_keys
    from modules.shopping.services import offers as offers_service

    monkeypatch.setattr(provider_keys, "kroger_credentials", lambda db: ("cid", "sec"))
    monkeypatch.setattr(provider_keys, "kroger_location_id", lambda db: "01400943")
    monkeypatch.setattr(offers_service, "search_usda_products", lambda q, limit=10, api_key=None: [])
    _wire(monkeypatch)

    result = offers_service.search_offers("paper towels", limit=5)
    providers = {o["provider"] for o in result["offers"]}
    assert "kroger" in providers
