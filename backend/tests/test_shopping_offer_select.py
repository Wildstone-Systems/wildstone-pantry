"""Selecting an offer creates/links the catalog item and records the snapshot (ADR 0013)."""

from __future__ import annotations

import httpx
import pytest

from app.core.config import settings
from modules.catalog.services import barcodes as barcodes_service


@pytest.fixture(autouse=True)
def _personal_mode(monkeypatch):
    monkeypatch.setattr(settings, "auth_enabled", False)
    # Offer resolution may probe the barcode path; keep it offline/deterministic.
    monkeypatch.setattr(barcodes_service, "lookup_external", lambda barcode, **_k: None)
    monkeypatch.setattr(barcodes_service, "lookup_external_merged", lambda barcode, **_k: None)


def _offer(**over) -> dict:
    base = {
        "provider": "food_db",
        "name": "Rao's Marinara Sauce",
        "brand": "Rao's",
        "upc": "0012345678905",
        "size": "24 oz",
        "quantity": 2,
        "unit": "each",
    }
    base.update(over)
    return base


def test_from_offer_creates_catalog_item_and_lists_it(client: httpx.Client) -> None:
    resp = client.post("/api/shopping-list/from-offer", json=_offer())
    assert resp.status_code == 201, resp.text
    entry = resp.json()

    assert entry["item_id"] is not None  # catalog item was created + linked
    assert entry["name"] == "Rao's Marinara Sauce"
    assert entry["quantity"] == "2.000"
    assert entry["chosen_offer"]["provider"] == "food_db"
    assert "chosen_at" in entry["chosen_offer"]  # snapshot timestamp

    # The catalog item exists with the offer's data.
    item = client.get(f"/api/items/{entry['item_id']}").json()
    assert item["name"] == "Rao's Marinara Sauce"
    assert item["barcode"] == "0012345678905"

    # And it's on the shopping list.
    names = [e["name"] for e in client.get("/api/shopping-list").json()["items"]]
    assert "Rao's Marinara Sauce" in names


def test_from_offer_without_upc_still_creates_a_named_item(client: httpx.Client) -> None:
    resp = client.post("/api/shopping-list/from-offer", json=_offer(upc=None, name="Store Brand Paper Towels"))
    assert resp.status_code == 201, resp.text
    entry = resp.json()
    assert entry["item_id"] is not None
    assert entry["name"] == "Store Brand Paper Towels"


def test_from_offer_resolves_an_existing_loose_need(client: httpx.Client) -> None:
    loose = client.post("/api/shopping-list", json={"name": "marinara"}).json()
    assert loose["item_id"] is None  # unresolved need

    resp = client.post("/api/shopping-list/from-offer", json=_offer(entry_id=loose["id"]))
    assert resp.status_code == 201, resp.text
    entry = resp.json()
    assert entry["id"] == loose["id"]  # same entry, now resolved
    assert entry["item_id"] is not None
    assert entry["chosen_offer"]["brand"] == "Rao's"

    # No duplicate entry was created.
    assert len(client.get("/api/shopping-list").json()["items"]) == 1


def test_from_offer_with_item_id_enriches_instead_of_duplicating(client: httpx.Client) -> None:
    """Shopping for a recipe's ingredient: link the offer to the existing item."""
    # A recipe-style ingredient item: name only, no barcode.
    ingredient = client.post("/api/items", json={"name": "kidney beans"}).json()
    items_before = len(client.get("/api/items").json()["items"])

    resp = client.post(
        "/api/shopping-list/from-offer",
        json=_offer(name="Smart Way Kidney Beans", upc="0001111060903", brand="Smart Way", item_id=ingredient["id"]),
    )
    assert resp.status_code == 201, resp.text
    entry = resp.json()

    # Linked to the SAME item — no duplicate created.
    assert entry["item_id"] == ingredient["id"]
    assert len(client.get("/api/items").json()["items"]) == items_before

    # The existing item got enriched with the offer's UPC + brand.
    got = client.get(f"/api/items/{ingredient['id']}").json()
    assert got["barcode"] == "0001111060903"
    assert got["brand"] == "Smart Way"


def test_from_offer_with_item_id_pulls_full_nutrition(client: httpx.Client, monkeypatch) -> None:
    """Enriching a recipe ingredient runs the provider cascade too — so the item
    gets nutrition/serving/density, not just the offer's barcode/brand/size."""
    from modules.catalog.services.barcode_providers import ProductResult

    def _fake_lookup(barcode, **_k):
        return ProductResult(
            barcode=barcode,
            name="Smart Way Kidney Beans",
            source="open_food_facts",
            nutrition={"energy-kcal_100g": 127, "proteins_100g": 8.7},
            serving_size_g=130,
        )

    monkeypatch.setattr(barcodes_service, "lookup_external", _fake_lookup)
    monkeypatch.setattr(barcodes_service, "lookup_external_merged", _fake_lookup)

    ingredient = client.post("/api/items", json={"name": "kidney beans"}).json()
    resp = client.post(
        "/api/shopping-list/from-offer",
        json=_offer(name="Smart Way Kidney Beans", upc="0001111060903", item_id=ingredient["id"]),
    )
    assert resp.status_code == 201, resp.text

    got = client.get(f"/api/items/{ingredient['id']}").json()
    assert got["barcode"] == "0001111060903"  # offer's UPC attached
    assert got["energy_kcal_100g"] == 127  # AND nutrition pulled from the cascade
    assert got["serving_size_g"] == 130


def test_from_offer_reuses_an_existing_catalog_item_by_barcode(client: httpx.Client) -> None:
    existing = client.post("/api/items", json={"name": "Rao's Marinara", "barcode": "0012345678905"}).json()
    resp = client.post("/api/shopping-list/from-offer", json=_offer())
    assert resp.status_code == 201
    assert resp.json()["item_id"] == existing["id"]  # linked, not duplicated
