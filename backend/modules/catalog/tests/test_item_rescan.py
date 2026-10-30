"""Re-querying an item against OFF fills gaps without overwriting edits."""

from __future__ import annotations

import httpx
import pytest

from modules.catalog.services import barcodes as barcodes_service
from modules.catalog.services.barcode_providers import ProductResult


def _off(barcode: str) -> ProductResult:
    return ProductResult(
        barcode=barcode,
        name="2% Milk",
        brand="Store Brand",
        size_description="1 gallon",
        size_quantity=1,
        size_unit="gal",
        source="open_food_facts",
        nutri_score="c",
        density_g_per_ml=1.03,
    )


def test_rescan_fills_missing_fields_and_reports_them(client: httpx.Client, monkeypatch) -> None:
    # A bare item with just a barcode — created before enrichment existed.
    created = client.post("/api/items", json={"name": "2% Milk", "barcode": "0070038303434"}).json()
    monkeypatch.setattr(barcodes_service, "lookup_external", lambda barcode, **_k: _off(barcode))
    monkeypatch.setattr(barcodes_service, "lookup_external_merged", lambda barcode, **_k: _off(barcode))

    resp = client.post(f"/api/items/{created['id']}/rescan")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "updated"
    assert {"size_quantity", "size_unit", "density_g_per_ml", "nutri_score"} <= set(body["filled"])
    assert body["item"]["size_unit"] == "gal"
    assert body["item"]["density_g_per_ml"] == 1.03


def test_rescan_never_overwrites_a_user_edited_value(client: httpx.Client, monkeypatch) -> None:
    created = client.post(
        "/api/items", json={"name": "2% Milk", "barcode": "0070038303434", "density_g_per_ml": 2.5}
    ).json()
    monkeypatch.setattr(barcodes_service, "lookup_external", lambda barcode, **_k: _off(barcode))
    monkeypatch.setattr(barcodes_service, "lookup_external_merged", lambda barcode, **_k: _off(barcode))

    client.post(f"/api/items/{created['id']}/rescan")
    got = client.get(f"/api/items/{created['id']}").json()
    assert got["density_g_per_ml"] == 2.5  # kept, not clobbered by OFF's 1.03


def test_rescan_of_item_without_a_barcode_is_a_clean_noop(client: httpx.Client) -> None:
    created = client.post("/api/items", json={"name": "Homemade Bread"}).json()
    resp = client.post(f"/api/items/{created['id']}/rescan")
    assert resp.status_code == 200
    assert resp.json()["status"] == "no_barcode"
    assert resp.json()["filled"] == []


def test_rescan_when_provider_has_nothing(client: httpx.Client, monkeypatch) -> None:
    created = client.post("/api/items", json={"name": "Obscure", "barcode": "0000000000000"}).json()
    monkeypatch.setattr(barcodes_service, "lookup_external", lambda barcode, **_k: None)
    monkeypatch.setattr(barcodes_service, "lookup_external_merged", lambda barcode, **_k: None)
    resp = client.post(f"/api/items/{created['id']}/rescan")
    assert resp.status_code == 200
    assert resp.json()["status"] == "not_found"


def test_rescan_missing_item_404(client: httpx.Client) -> None:
    assert client.post("/api/items/00000000-0000-0000-0000-000000000000/rescan").status_code == 404
