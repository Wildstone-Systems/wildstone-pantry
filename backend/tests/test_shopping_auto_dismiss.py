"""Deleting an auto (restock) shopping entry dismisses it — it must stay off the
list until the item's stock recovers, instead of being re-added on every read."""

from __future__ import annotations

import httpx
import pytest

from app.core.config import settings


@pytest.fixture(autouse=True)
def _personal(monkeypatch):
    monkeypatch.setattr(settings, "auth_enabled", False)


def _names_item_ids(client: httpx.Client) -> list[str]:
    return [e["item_id"] for e in client.get("/api/shopping-list").json()["items"]]


def test_deleting_an_auto_entry_makes_it_stay_gone(client: httpx.Client) -> None:
    # Below its restock threshold with no stock → an auto entry appears.
    item = client.post("/api/items", json={"name": "Broccoli", "restock_threshold": 5}).json()
    auto = next(e for e in client.get("/api/shopping-list").json()["items"] if e["item_id"] == item["id"])
    assert auto["source"] == "auto"

    client.delete(f"/api/shopping-list/{auto['id']}")
    assert item["id"] not in _names_item_ids(client)  # gone
    assert item["id"] not in _names_item_ids(client)  # still gone on a second read (not re-added)


def test_dismissal_clears_after_stock_recovers(client: httpx.Client) -> None:
    loc = client.post("/api/locations", json={"name": "Pantry"}).json()
    item = client.post("/api/items", json={"name": "Broccoli2", "restock_threshold": 5}).json()

    auto = next(e for e in client.get("/api/shopping-list").json()["items"] if e["item_id"] == item["id"])
    client.delete(f"/api/shopping-list/{auto['id']}")
    assert item["id"] not in _names_item_ids(client)

    # Restock above threshold → the dismissal is cleared.
    client.post(
        "/api/inventory/add",
        json={"item_id": item["id"], "location_id": loc["id"], "quantity": 10, "unit": "each"},
    )
    assert item["id"] not in _names_item_ids(client)  # not below threshold → still no entry

    # Drop below again → it re-triggers (dismissal no longer suppresses it).
    client.post(
        "/api/inventory/remove",
        json={"item_id": item["id"], "location_id": loc["id"], "quantity": 10, "unit": "each"},
    )
    entries = client.get("/api/shopping-list").json()["items"]
    assert any(e["item_id"] == item["id"] and e["source"] == "auto" for e in entries)


def test_add_by_item_id_is_idempotent_no_duplicate(client: httpx.Client) -> None:
    item = client.post("/api/items", json={"name": "Nutella"}).json()
    a = client.post("/api/shopping-list", json={"name": "Nutella", "item_id": item["id"], "quantity": 1}).json()
    b = client.post("/api/shopping-list", json={"name": "Nutella", "item_id": item["id"], "quantity": 1}).json()
    assert a["id"] == b["id"]  # same entry, not a duplicate
    entries = [e for e in client.get("/api/shopping-list").json()["items"] if e["item_id"] == item["id"]]
    assert len(entries) == 1
