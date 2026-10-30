"""USDA FoodData Central provider: mapping, gtin matching, cascade, dormancy."""

from __future__ import annotations

import httpx
import pytest

from app.core.config import settings
from modules.catalog.services import barcode_providers as bp
from modules.catalog.services.barcode_providers import (
    ProviderRateLimited,
    lookup_external,
    lookup_usda_fdc,
)

_UPC = "072730221109"

_USDA_FOOD = {
    "foods": [
        {
            "description": "2% REDUCED FAT MILK",
            "gtinUpc": _UPC,
            "brandName": "Store Brand",
            "packageWeight": "1 gal/3.78 L",
            "servingSize": 240,
            "servingSizeUnit": "ml",
            "ingredients": "REDUCED FAT MILK, VITAMIN A, VITAMIN D3",
            "foodNutrients": [
                {"nutrientNumber": "208", "value": 50},
                {"nutrientNumber": "203", "value": 3.4},
                {"nutrientNumber": "204", "value": 2.0},
                {"nutrientNumber": "205", "value": 4.8},
                {"nutrientNumber": "307", "value": 44},  # sodium mg → 0.044 g
            ],
        }
    ]
}


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    monkeypatch.setattr(bp.time, "sleep", lambda _s: None)
    monkeypatch.setattr(settings, "usda_fdc_api_key", "test-key")


def _resp(status: int, body: dict) -> httpx.Response:
    return httpx.Response(status, json=body, request=httpx.Request("GET", "https://fdc.test"))


def test_dormant_without_a_key(monkeypatch) -> None:
    monkeypatch.setattr(settings, "usda_fdc_api_key", "")
    # No HTTP call should even be attempted.
    monkeypatch.setattr(bp.httpx, "get", lambda *a, **k: pytest.fail("should not call USDA without a key"))
    assert lookup_usda_fdc(_UPC) is None


def test_maps_a_branded_food(monkeypatch) -> None:
    monkeypatch.setattr(bp.httpx, "get", lambda *a, **k: _resp(200, _USDA_FOOD))
    r = lookup_usda_fdc(_UPC)
    assert r is not None
    assert r.name == "2% Reduced Fat Milk"  # title-cased
    assert r.brand == "Store Brand"
    assert r.source == "usda_fdc"
    assert (r.size_quantity, r.size_unit) == (1.0, "gal")
    assert r.nutrition["energy-kcal_100g"] == 50
    assert r.nutrition["sodium_100g"] == pytest.approx(0.044)  # mg → g


def test_ignores_a_fuzzy_hit_with_a_different_gtin(monkeypatch) -> None:
    other = {"foods": [{**_USDA_FOOD["foods"][0], "gtinUpc": "999999999999"}]}
    monkeypatch.setattr(bp.httpx, "get", lambda *a, **k: _resp(200, other))
    assert lookup_usda_fdc(_UPC) is None  # never a wrong-product match


def test_api_key_is_not_logged_on_error(monkeypatch, caplog) -> None:
    monkeypatch.setattr(bp.httpx, "get", lambda *a, **k: _resp(500, {}))
    lookup_usda_fdc(_UPC)
    assert "test-key" not in caplog.text


def test_cascade_falls_back_to_usda_when_off_misses(monkeypatch) -> None:
    monkeypatch.setattr(bp, "lookup_open_food_facts", lambda bc: None)
    monkeypatch.setattr(bp.httpx, "get", lambda *a, **k: _resp(200, _USDA_FOOD))
    r = lookup_external(_UPC)
    assert r is not None and r.source == "usda_fdc"


def test_cascade_tries_usda_even_when_off_is_rate_limited(monkeypatch) -> None:
    def off_throttled(_bc):
        raise ProviderRateLimited("off")

    monkeypatch.setattr(bp, "lookup_open_food_facts", off_throttled)
    monkeypatch.setattr(bp.httpx, "get", lambda *a, **k: _resp(200, _USDA_FOOD))
    r = lookup_external(_UPC)
    assert r is not None and r.source == "usda_fdc"  # not raised as rate_limited


def test_cascade_raises_rate_limited_only_when_all_throttled(monkeypatch) -> None:
    def off_throttled(_bc):
        raise ProviderRateLimited("off")

    monkeypatch.setattr(bp, "lookup_open_food_facts", off_throttled)
    monkeypatch.setattr(bp.httpx, "get", lambda *a, **k: _resp(429, {}))
    with pytest.raises(ProviderRateLimited):
        lookup_external(_UPC)
