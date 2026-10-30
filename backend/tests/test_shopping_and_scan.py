"""Shopping list auto-reconcile + scan-station moves — step 3 (close the loop)."""

from __future__ import annotations

import httpx


def _headers(client: httpx.Client) -> dict[str, str]:
    client.post("/api/auth/bootstrap-admin", json={"email": "admin@pantryos.local", "password": "TestPass123!"})
    login = client.post("/api/auth/login", json={"email": "admin@pantryos.local", "password": "TestPass123!"})
    return {"Authorization": f"Bearer {login.json()['token']}"}


def _add(client, h, item_id, loc, qty):
    return client.post(
        "/api/inventory/add",
        json={"item_id": item_id, "location_id": loc, "quantity": qty, "unit": "each"},
        headers=h,
    )


def _names(resp):
    return {(e["name"], e["source"]) for e in resp.json()["items"]}


def test_shopping_list_auto_reconciles_from_restock_threshold(client: httpx.Client) -> None:
    h = _headers(client)
    milk = client.post("/api/items", json={"name": "Milk", "restock_threshold": 3}, headers=h).json()["id"]
    loc = client.post("/api/locations", json={"name": "Fridge"}, headers=h).json()["id"]

    # On-hand 0 < 3 -> Milk auto-appears on the list.
    assert ("Milk", "auto") in _names(client.get("/api/shopping-list", headers=h))

    # Restock above threshold -> auto entry clears.
    _add(client, h, milk, loc, 5)
    assert ("Milk", "auto") not in _names(client.get("/api/shopping-list", headers=h))

    # Consume back below threshold -> auto entry returns.
    client.post(
        "/api/inventory/remove",
        json={"item_id": milk, "location_id": loc, "quantity": 3, "unit": "each"},
        headers=h,
    )
    assert ("Milk", "auto") in _names(client.get("/api/shopping-list", headers=h))


def test_manual_entries_persist_and_can_be_marked_purchased(client: httpx.Client) -> None:
    h = _headers(client)
    added = client.post("/api/shopping-list", json={"name": "Paper Towels", "quantity": 2}, headers=h)
    assert added.status_code == 201
    entry_id = added.json()["id"]
    assert added.json()["source"] == "manual"

    # A reconcile pass (GET) must not remove the manual entry.
    assert ("Paper Towels", "manual") in _names(client.get("/api/shopping-list", headers=h))

    marked = client.patch(f"/api/shopping-list/{entry_id}", json={"status": "purchased"}, headers=h)
    assert marked.status_code == 200
    assert marked.json()["status"] == "purchased"

    assert client.delete(f"/api/shopping-list/{entry_id}", headers=h).status_code == 204
    assert ("Paper Towels", "manual") not in _names(client.get("/api/shopping-list", headers=h))


def test_scan_station_moves_are_attributed_and_ledgered(client: httpx.Client) -> None:
    h = _headers(client)
    profile = client.post("/api/household/profiles", json={"display_name": "Alex"}, headers=h).json()["id"]
    client.post("/api/items", json={"name": "Beans", "barcode": "0123456789012"}, headers=h)
    loc = client.post("/api/locations", json={"name": "Pantry"}, headers=h).json()["id"]
    hp = {**h, "X-Household-Profile": profile}

    # Scan IN 2 (add).
    scan_in = client.post(
        "/api/inventory/scan",
        json={"barcode": "0123456789012", "location_id": loc, "direction": "in", "quantity": 2},
        headers=hp,
    )
    assert scan_in.status_code == 200
    assert scan_in.json()["direction"] == "in"
    assert str(scan_in.json()["inventory"][0]["quantity"]).startswith("2")

    # Scan OUT 1 (consume).
    scan_out = client.post(
        "/api/inventory/scan",
        json={"barcode": "0123456789012", "location_id": loc, "direction": "out", "quantity": 1},
        headers=hp,
    )
    assert str(scan_out.json()["inventory"][0]["quantity"]).startswith("1")

    # Events are attributed to the scanning profile.
    events = client.get(f"/api/inventory/events?location_id={loc}", headers=h).json()["events"]
    assert [e["event_type"] for e in events] == ["consumed", "purchased"]
    assert all(e["actor"] == profile for e in events)


def test_scan_unknown_barcode_returns_404(client: httpx.Client) -> None:
    h = _headers(client)
    loc = client.post("/api/locations", json={"name": "Nowhere"}, headers=h).json()["id"]
    resp = client.post(
        "/api/inventory/scan",
        json={"barcode": "999999999", "location_id": loc, "direction": "in", "quantity": 1},
        headers=h,
    )
    assert resp.status_code == 404
