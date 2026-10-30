"""Item.stock_mode — ADR 0014 Phase 1, step 1.

The counted/measured default is inferred from the package unit at create time and
is user-overridable.
"""

from __future__ import annotations

import httpx
import pytest

from app.core.config import settings
from modules.catalog.services.units import infer_stock_mode


@pytest.fixture(autouse=True)
def _personal(monkeypatch):
    monkeypatch.setattr(settings, "auth_enabled", False)


def test_infer_stock_mode_heuristic() -> None:
    assert infer_stock_mode("gal") == "measured"
    assert infer_stock_mode("oz") == "measured"
    assert infer_stock_mode("ml") == "measured"
    assert infer_stock_mode("ea") == "counted"
    assert infer_stock_mode("ct") == "counted"
    assert infer_stock_mode("") == "counted"
    assert infer_stock_mode(None) == "counted"


def test_create_infers_measured_from_volume_unit(client: httpx.Client) -> None:
    item = client.post("/api/items", json={"name": "Milk", "size_quantity": 1, "size_unit": "gal"}).json()
    assert item["stock_mode"] == "measured"


def test_create_infers_counted_from_count_unit(client: httpx.Client) -> None:
    item = client.post("/api/items", json={"name": "Eggs", "size_quantity": 12, "size_unit": "ea"}).json()
    assert item["stock_mode"] == "counted"


def test_create_respects_explicit_stock_mode(client: httpx.Client) -> None:
    # A can you use whole, despite a weight unit.
    item = client.post(
        "/api/items", json={"name": "Beans", "size_unit": "oz", "stock_mode": "counted"}
    ).json()
    assert item["stock_mode"] == "counted"


def test_update_can_change_stock_mode(client: httpx.Client) -> None:
    item = client.post("/api/items", json={"name": "Flour", "size_unit": "ea"}).json()
    assert item["stock_mode"] == "counted"
    updated = client.put(f"/api/items/{item['id']}", json={"stock_mode": "measured"}).json()
    assert updated["stock_mode"] == "measured"


def test_invalid_stock_mode_is_rejected(client: httpx.Client) -> None:
    resp = client.post("/api/items", json={"name": "Bad", "stock_mode": "weighed"})
    assert resp.status_code == 422
