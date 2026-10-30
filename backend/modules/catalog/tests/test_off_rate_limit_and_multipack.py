"""OFF 429 handling (retry, then honest 'rate_limited') and multipack sizes.

A whole-catalog rescan bursts the OFF product API, which 429s. Treating that as
'not found' made items silently populate nothing — the reported bug.
"""

from __future__ import annotations

import httpx
import pytest

from modules.catalog.services import barcode_providers as bp
from modules.catalog.services import barcodes as barcodes_service
from modules.catalog.services.barcode_providers import (
    ProviderRateLimited,
    _parse_quantity,
    lookup_open_food_facts,
)


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    """Don't actually wait during backoff in tests."""
    monkeypatch.setattr(bp.time, "sleep", lambda _s: None)


def _resp(status: int, json_body: dict | None = None) -> httpx.Response:
    return httpx.Response(status, json=json_body or {}, request=httpx.Request("GET", "https://off.test"))


def test_429_is_retried_then_succeeds(monkeypatch) -> None:
    calls = {"n": 0}
    product = {"status": 1, "product": {"code": "123", "product_name": "Cola", "quantity": "33 cl"}}

    def fake_get(*_a, **_k):
        calls["n"] += 1
        return _resp(429) if calls["n"] == 1 else _resp(200, product)

    monkeypatch.setattr(bp.httpx, "get", fake_get)
    result = lookup_open_food_facts("123")
    assert result is not None
    assert result.name == "Cola"
    assert calls["n"] == 2  # first 429, retry succeeded


def test_persistent_429_raises_rate_limited(monkeypatch) -> None:
    monkeypatch.setattr(bp.httpx, "get", lambda *_a, **_k: _resp(429))
    with pytest.raises(ProviderRateLimited):
        lookup_open_food_facts("123")


def test_rescan_reports_rate_limited_not_not_found(client: httpx.Client, monkeypatch) -> None:
    created = client.post("/api/items", json={"name": "Cola", "barcode": "5449000000996"}).json()

    def boom(_barcode, **_k):
        raise ProviderRateLimited("5449000000996")

    monkeypatch.setattr(barcodes_service, "lookup_external", boom)
    monkeypatch.setattr(barcodes_service, "lookup_external_merged", boom)
    resp = client.post(f"/api/items/{created['id']}/rescan")
    assert resp.status_code == 200
    assert resp.json()["status"] == "rate_limited"
    # Must NOT have stamped a check time (the data may still exist).
    assert client.get(f"/api/items/{created['id']}").json()["off_last_checked_at"] is None


def test_scan_degrades_gracefully_when_rate_limited(client: httpx.Client, monkeypatch) -> None:
    def boom(_barcode, **_k):
        raise ProviderRateLimited("x")

    monkeypatch.setattr(barcodes_service, "lookup_external", boom)
    monkeypatch.setattr(barcodes_service, "lookup_external_merged", boom)
    resp = client.post("/api/barcodes/scan", json={"barcode": "999999999999", "auto_create": True})
    assert resp.status_code == 200
    assert resp.json()["found"] is False


@pytest.mark.parametrize(
    ("text", "qty", "unit"),
    [
        ("2 x 150 g", 300.0, "g"),
        ("6x330ml", 1980.0, "ml"),
        ("4 × 1 L", 4.0, "l"),
    ],
)
def test_multipack_quantity_totals(text: str, qty: float, unit: str) -> None:
    got_qty, got_unit, _ = _parse_quantity(text)
    assert got_qty == qty
    assert got_unit == unit
