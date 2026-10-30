"""Scan station location resolution + best-by (feedback /scan-station).

The station no longer forces a location: a consume with the item in one place
just works, with several it asks, and an add can carry a best-by right there."""

from __future__ import annotations

import httpx


def _headers(client: httpx.Client) -> dict[str, str]:
    client.post("/api/auth/bootstrap-admin", json={"email": "admin@pantryos.local", "password": "TestPass123!"})
    login = client.post("/api/auth/login", json={"email": "admin@pantryos.local", "password": "TestPass123!"})
    return {"Authorization": f"Bearer {login.json()['token']}"}


def _item(client, h, name, barcode) -> str:
    return client.post("/api/items", json={"name": name, "barcode": barcode}, headers=h).json()["id"]


def _loc(client, h, name) -> str:
    return client.post("/api/locations", json={"name": name}, headers=h).json()["id"]


def _add(client, h, item_id, loc, qty=5):
    client.post(
        "/api/inventory/add",
        json={"item_id": item_id, "location_id": loc, "quantity": qty, "unit": "each"},
        headers=h,
    )


def test_consume_without_location_auto_resolves_single(client: httpx.Client) -> None:
    h = _headers(client)
    item = _item(client, h, "Chips", "0111111111111")
    pantry = _loc(client, h, "Pantry")
    _add(client, h, item, pantry, 5)

    # No location given → the one place it lives is used automatically.
    resp = client.post(
        "/api/inventory/scan",
        json={"barcode": "0111111111111", "direction": "out", "quantity": 1},
        headers=h,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "done"
    assert body["resolved_location_id"] == pantry
    assert float(body["inventory"][0]["quantity"]) == 4.0


def test_consume_without_location_prompts_when_multiple(client: httpx.Client) -> None:
    h = _headers(client)
    item = _item(client, h, "Soda", "0222222222222")
    fridge = _loc(client, h, "Fridge")
    garage = _loc(client, h, "Garage")
    _add(client, h, item, fridge, 2)
    _add(client, h, item, garage, 2)

    resp = client.post(
        "/api/inventory/scan",
        json={"barcode": "0222222222222", "direction": "out", "quantity": 1},
        headers=h,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "needs_location"
    ids = {c["location_id"] for c in body["candidate_locations"]}
    assert ids == {fridge, garage}
    # Nothing was consumed while awaiting the choice.
    assert float(client.get(f"/api/inventory?location_id={fridge}", headers=h).json()["items"][0]["quantity"]) == 2.0


def test_consume_without_location_errors_when_nowhere(client: httpx.Client) -> None:
    h = _headers(client)
    _item(client, h, "Ghost", "0333333333333")
    resp = client.post(
        "/api/inventory/scan",
        json={"barcode": "0333333333333", "direction": "out", "quantity": 1},
        headers=h,
    )
    assert resp.status_code == 400
    assert "isn't in stock" in resp.json()["detail"]


def test_add_with_best_by_dates_the_stock(client: httpx.Client) -> None:
    h = _headers(client)
    item = _item(client, h, "Milk", "0444444444444")
    fridge = _loc(client, h, "Fridge")

    resp = client.post(
        "/api/inventory/scan",
        json={
            "barcode": "0444444444444",
            "location_id": fridge,
            "direction": "in",
            "quantity": 1,
            "expiration_date": "2026-08-10",
        },
        headers=h,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "done"

    lots = client.get("/api/inventory/lots", headers=h).json()["lots"]
    milk_lot = next(lot for lot in lots if lot["item_name"] == "Milk")
    assert milk_lot["expiration_date"] == "2026-08-10"
