"""Shopping-list offer search (ADR 0013, Phase 1)."""

from __future__ import annotations

import httpx
import pytest

from app.core.config import settings
from modules.catalog.services.barcode_providers import ProviderRateLimited
from modules.shopping.services import offers as offers_service


@pytest.fixture(autouse=True)
def _personal_mode(monkeypatch):
    monkeypatch.setattr(settings, "auth_enabled", False)


def test_offers_map_food_db_rows(monkeypatch):
    monkeypatch.setattr(
        offers_service,
        "search_usda_products",
        lambda q, limit=10, api_key=None: [
            {"name": "Marinara Sauce", "brand": "Rao's", "upc": "00012345678905", "size": "24 oz", "source": "usda"}
        ],
    )
    result = offers_service.search_offers("marinara", limit=5)
    assert result["query"] == "marinara"
    assert result["throttled"] == []
    off = result["offers"][0]
    assert off["provider"] == "food_db"
    assert off["name"] == "Marinara Sauce"
    assert off["upc"] == "00012345678905"
    assert off["price"] is None  # food DB has no price


def test_blank_query_returns_nothing(monkeypatch):
    monkeypatch.setattr(offers_service, "search_usda_products", lambda q, limit=10, api_key=None: pytest.fail("should not search"))
    assert offers_service.search_offers("   ")["offers"] == []


def test_rate_limited_provider_is_reported_not_fatal(monkeypatch):
    def throttled(q, limit=10, api_key=None):
        raise ProviderRateLimited("usda")

    monkeypatch.setattr(offers_service, "search_usda_products", throttled)
    result = offers_service.search_offers("milk")
    assert result["offers"] == []
    assert result["throttled"] == ["food_db"]


def test_search_endpoint(client: httpx.Client, monkeypatch):
    monkeypatch.setattr(
        offers_service,
        "search_usda_products",
        lambda q, limit=10, api_key=None: [{"name": "Milk", "brand": "Prairie Farms", "upc": "1", "size": "1 gal", "source": "usda"}],
    )
    resp = client.get("/api/shopping-list/search", params={"q": "milk"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["query"] == "milk"
    assert body["offers"][0]["name"] == "Milk"


def test_search_endpoint_requires_a_query(client: httpx.Client):
    assert client.get("/api/shopping-list/search").status_code == 422
