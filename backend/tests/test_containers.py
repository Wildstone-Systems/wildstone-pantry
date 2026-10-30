"""Containers — named sub-divisions of a location, per-lot placement (GH #9).

Ported onto this trunk's event-sourced ledger: placement is recorded on the
(immutable) event and folded onto the lot projection.
"""

from __future__ import annotations

import httpx


def _headers(client: httpx.Client) -> dict[str, str]:
    client.post("/api/auth/bootstrap-admin", json={"email": "admin@pantryos.local", "password": "TestPass123!"})
    login = client.post("/api/auth/login", json={"email": "admin@pantryos.local", "password": "TestPass123!"})
    return {"Authorization": f"Bearer {login.json()['token']}"}


def test_container_crud_and_lot_placement(client: httpx.Client) -> None:
    from app.db import SessionLocal
    from modules.inventory.models import InventoryLot

    h = _headers(client)
    item_id = client.post("/api/items", json={"name": "Beans"}, headers=h).json()["id"]
    pantry = client.post("/api/locations", json={"name": "Pantry"}, headers=h).json()["id"]

    # Create a container in the pantry.
    shelf = client.post("/api/inventory/containers", json={"location_id": pantry, "name": "Shelf A"}, headers=h)
    assert shelf.status_code == 201
    shelf_id = shelf.json()["id"]

    # List containers for the location.
    listed = client.get(f"/api/inventory/containers?location_id={pantry}", headers=h).json()["containers"]
    assert [c["id"] for c in listed] == [shelf_id]

    # Add stock placed in the container → the resulting lot records the container.
    add = client.post(
        "/api/inventory/add",
        json={"item_id": item_id, "location_id": pantry, "quantity": 4, "unit": "can", "container_id": shelf_id},
        headers=h,
    )
    assert add.status_code == 200

    db = SessionLocal()
    try:
        lots = db.query(InventoryLot).filter(InventoryLot.item_id == item_id).all()
        assert len(lots) == 1
        assert str(lots[0].container_id) == shelf_id
    finally:
        db.close()


def test_add_rejects_container_from_other_location(client: httpx.Client) -> None:
    h = _headers(client)
    item_id = client.post("/api/items", json={"name": "Rice"}, headers=h).json()["id"]
    pantry = client.post("/api/locations", json={"name": "Pantry"}, headers=h).json()["id"]
    fridge = client.post("/api/locations", json={"name": "Fridge"}, headers=h).json()["id"]
    shelf = client.post("/api/inventory/containers", json={"location_id": pantry, "name": "Shelf"}, headers=h).json()

    # Placing fridge stock into a pantry container is a cross-location error.
    resp = client.post(
        "/api/inventory/add",
        json={"item_id": item_id, "location_id": fridge, "quantity": 1, "container_id": shelf["id"]},
        headers=h,
    )
    assert resp.status_code == 400


def test_delete_container(client: httpx.Client) -> None:
    h = _headers(client)
    loc = client.post("/api/locations", json={"name": "Pantry"}, headers=h).json()["id"]
    c = client.post("/api/inventory/containers", json={"location_id": loc, "name": "Bin"}, headers=h).json()
    assert client.delete(f"/api/inventory/containers/{c['id']}", headers=h).status_code == 204
    assert client.get(f"/api/inventory/containers?location_id={loc}", headers=h).json()["containers"] == []
