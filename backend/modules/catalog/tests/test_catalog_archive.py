"""Catalog items and locations archive (soft-delete) instead of vanishing.

Archiving keeps the row so recipe ingredients, inventory and history still
resolve; the archived row drops out of default lists but reappears with
``include_inactive=true`` and can be reactivated.
"""

from __future__ import annotations

import httpx


def test_new_items_and_locations_default_to_active(client: httpx.Client) -> None:
    item = client.post("/api/items", json={"name": "Rice"}).json()
    location = client.post("/api/locations", json={"name": "Pantry"}).json()
    assert item["is_active"] is True
    assert location["is_active"] is True


def test_archived_item_is_hidden_by_default_but_visible_with_flag(client: httpx.Client) -> None:
    item_id = client.post("/api/items", json={"name": "Old Cereal"}).json()["id"]

    archived = client.put(f"/api/items/{item_id}", json={"is_active": False})
    assert archived.status_code == 200, archived.text
    assert archived.json()["is_active"] is False

    default_list = client.get("/api/items").json()["items"]
    assert all(row["id"] != item_id for row in default_list)

    with_inactive = client.get("/api/items", params={"include_inactive": True}).json()["items"]
    assert any(row["id"] == item_id for row in with_inactive)


def test_archiving_an_item_leaves_other_fields_untouched(client: httpx.Client) -> None:
    item_id = client.post("/api/items", json={"name": "Olive Oil", "brand": "Bertolli"}).json()["id"]

    client.put(f"/api/items/{item_id}", json={"is_active": False})
    row = client.get(f"/api/items/{item_id}").json()
    assert row["name"] == "Olive Oil"
    assert row["brand"] == "Bertolli"
    assert row["is_active"] is False


def test_item_can_be_reactivated(client: httpx.Client) -> None:
    item_id = client.post("/api/items", json={"name": "Seasonal Item"}).json()["id"]
    client.put(f"/api/items/{item_id}", json={"is_active": False})

    reactivated = client.put(f"/api/items/{item_id}", json={"is_active": True})
    assert reactivated.status_code == 200
    assert reactivated.json()["is_active"] is True
    assert any(row["id"] == item_id for row in client.get("/api/items").json()["items"])


def test_archived_location_is_hidden_by_default_but_reactivatable(client: httpx.Client) -> None:
    location_id = client.post("/api/locations", json={"name": "Garage"}).json()["id"]

    archived = client.put(f"/api/locations/{location_id}", json={"is_active": False})
    assert archived.status_code == 200, archived.text
    assert archived.json()["is_active"] is False

    assert all(loc["id"] != location_id for loc in client.get("/api/locations").json()["locations"])
    with_inactive = client.get("/api/locations", params={"include_inactive": True}).json()["locations"]
    assert any(loc["id"] == location_id for loc in with_inactive)

    reactivated = client.put(f"/api/locations/{location_id}", json={"is_active": True})
    assert reactivated.json()["is_active"] is True
    assert any(loc["id"] == location_id for loc in client.get("/api/locations").json()["locations"])


def test_editing_a_location_preserves_active_state(client: httpx.Client) -> None:
    location_id = client.post("/api/locations", json={"name": "Shelf", "description": "top"}).json()["id"]
    client.put(f"/api/locations/{location_id}", json={"is_active": False})

    # A plain name/description edit must not silently un-archive the location.
    renamed = client.put(f"/api/locations/{location_id}", json={"name": "Shelf A"})
    assert renamed.json()["name"] == "Shelf A"
    assert renamed.json()["is_active"] is False
