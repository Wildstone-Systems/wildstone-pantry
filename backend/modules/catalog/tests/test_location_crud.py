"""Locations support edit + delete, at parity with items.

The /catalog page needs to update and remove locations, not just create them.
Delete is guarded: a location that still holds stock returns 409 so cascading
deletes never silently wipe real inventory.
"""

from __future__ import annotations

import httpx


def _auth_headers(client: httpx.Client) -> dict[str, str]:
    client.post("/api/auth/bootstrap-admin", json={"email": "admin@pantryos.local", "password": "TestPass123!"})
    login = client.post("/api/auth/login", json={"email": "admin@pantryos.local", "password": "TestPass123!"})
    assert login.status_code == 200, login.text
    return {"Authorization": f"Bearer {login.json()['token']}"}


def test_update_location_changes_name_and_description(client: httpx.Client) -> None:
    created = client.post("/api/locations", json={"name": "Garage", "description": "cold"})
    assert created.status_code == 201, created.text
    location_id = created.json()["id"]

    updated = client.put(
        f"/api/locations/{location_id}",
        json={"name": "Garage Shelf", "description": "cold and dry"},
    )
    assert updated.status_code == 200, updated.text
    body = updated.json()
    assert body["name"] == "Garage Shelf"
    assert body["description"] == "cold and dry"


def test_update_location_can_clear_description(client: httpx.Client) -> None:
    created = client.post("/api/locations", json={"name": "Pantry", "description": "dry goods"})
    location_id = created.json()["id"]

    updated = client.put(f"/api/locations/{location_id}", json={"description": None})
    assert updated.status_code == 200, updated.text
    assert updated.json()["description"] is None
    # Name was omitted, so it must be untouched.
    assert updated.json()["name"] == "Pantry"


def test_update_missing_location_is_404(client: httpx.Client) -> None:
    missing = "00000000-0000-0000-0000-000000000000"
    resp = client.put(f"/api/locations/{missing}", json={"name": "Nope"})
    assert resp.status_code == 404


def test_update_to_duplicate_name_is_409(client: httpx.Client) -> None:
    client.post("/api/locations", json={"name": "Fridge"})
    other = client.post("/api/locations", json={"name": "Freezer"}).json()["id"]

    resp = client.put(f"/api/locations/{other}", json={"name": "Fridge"})
    assert resp.status_code == 409


def test_delete_empty_location_succeeds(client: httpx.Client) -> None:
    created = client.post("/api/locations", json={"name": "Temporary"})
    location_id = created.json()["id"]

    resp = client.delete(f"/api/locations/{location_id}")
    assert resp.status_code == 204, resp.text

    listed = client.get("/api/locations").json()["locations"]
    assert all(loc["id"] != location_id for loc in listed)


def test_delete_missing_location_is_404(client: httpx.Client) -> None:
    missing = "00000000-0000-0000-0000-000000000000"
    resp = client.delete(f"/api/locations/{missing}")
    assert resp.status_code == 404


def test_delete_location_with_stock_is_blocked(client: httpx.Client) -> None:
    location_id = client.post("/api/locations", json={"name": "Cellar"}).json()["id"]
    item_id = client.post("/api/items", json={"name": "Canned Beans"}).json()["id"]

    add = client.post(
        "/api/inventory/add",
        json={"item_id": item_id, "location_id": location_id, "quantity": 6, "unit": "each"},
        headers=_auth_headers(client),
    )
    assert add.status_code == 200, add.text

    blocked = client.delete(f"/api/locations/{location_id}")
    assert blocked.status_code == 409, blocked.text
    assert "archive" in blocked.json()["detail"].lower()

    # Still present after a blocked delete.
    listed = client.get("/api/locations").json()["locations"]
    assert any(loc["id"] == location_id for loc in listed)


def test_delete_location_with_only_history_is_blocked(client: httpx.Client) -> None:
    """Add-then-consume leaves current stock at zero but keeps transaction
    history; deleting would cascade that history away, so it must be blocked."""
    headers = _auth_headers(client)
    location_id = client.post("/api/locations", json={"name": "Old Cellar"}).json()["id"]
    item_id = client.post("/api/items", json={"name": "Flour"}).json()["id"]

    add = client.post(
        "/api/inventory/add",
        json={"item_id": item_id, "location_id": location_id, "quantity": 4, "unit": "each"},
        headers=headers,
    )
    assert add.status_code == 200, add.text
    removed = client.post(
        "/api/inventory/remove",
        json={"item_id": item_id, "location_id": location_id, "quantity": 4, "unit": "each", "reason": "used"},
        headers=headers,
    )
    assert removed.status_code == 200, removed.text

    # Current on-hand is zero, but the add/remove history remains.
    blocked = client.delete(f"/api/locations/{location_id}")
    assert blocked.status_code == 409, blocked.text
    assert "archive" in blocked.json()["detail"].lower()
