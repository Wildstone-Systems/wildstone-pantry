"""Rescan re-infers stock_mode when it backfills a size (ADR 0014 follow-up).

A spice created 'counted' because OFF had no size should become 'measured' once a
rescan fills in a weight/volume size — otherwise it stays wrongly counted.
"""

from __future__ import annotations

import httpx
import pytest

from app.core.config import settings
from modules.catalog.services import barcodes as barcodes_service
from modules.catalog.services.barcode_providers import ProductResult


@pytest.fixture(autouse=True)
def _personal(monkeypatch):
    monkeypatch.setattr(settings, "auth_enabled", False)


def test_rescan_upgrades_counted_to_measured_when_size_filled(client: httpx.Client, monkeypatch) -> None:
    monkeypatch.setattr(
        barcodes_service,
        "lookup_external_merged",
        lambda barcode, **_k: ProductResult(
            barcode=barcode, name="Cumin", source="usda_fdc", size_quantity=2.0, size_unit="oz", size_description="2 oz"
        ),
    )
    item = client.post("/api/items", json={"name": "Cumin", "barcode": "0001112223", "stock_mode": "counted"}).json()
    assert item["stock_mode"] == "counted" and item["size_quantity"] is None

    client.post(f"/api/items/{item['id']}/rescan")

    got = client.get(f"/api/items/{item['id']}").json()
    assert got["size_unit"] == "oz"  # size backfilled
    assert got["stock_mode"] == "measured"  # ...and mode upgraded


def test_rescan_does_not_downgrade_a_counted_item_without_a_measured_size(client: httpx.Client, monkeypatch) -> None:
    # A pill bottle: provider has no weight/volume size → stays counted.
    monkeypatch.setattr(
        barcodes_service,
        "lookup_external_merged",
        lambda barcode, **_k: ProductResult(barcode=barcode, name="Iron Pills", source="open_food_facts"),
    )
    item = client.post("/api/items", json={"name": "Iron Pills", "barcode": "0002223334"}).json()
    client.post(f"/api/items/{item['id']}/rescan")
    got = client.get(f"/api/items/{item['id']}").json()
    assert got["stock_mode"] == "counted"
