from __future__ import annotations

import os
import uuid

import httpx


API_BASE = os.getenv("API_BASE_URL", "http://localhost:8000")


def _auth_headers() -> dict[str, str]:
    email = os.getenv("TEST_ADMIN_EMAIL", "admin@pantryos.local")
    password = os.getenv("TEST_ADMIN_PASSWORD", "TestPass123!")
    httpx.post(f"{API_BASE}/api/auth/bootstrap-admin", json={"email": email, "password": password}, timeout=10)
    login = httpx.post(f"{API_BASE}/api/auth/login", json={"email": email, "password": password}, timeout=10)
    assert login.status_code == 200
    headers = {"Authorization": f"Bearer {login.json()['token']}"}
    return headers


def test_phase1_inventory_flow() -> None:
    headers = _auth_headers()
    suffix = uuid.uuid4().hex[:8]
    barcode = f"PHASE1-{suffix}"

    create_item = httpx.post(
        f"{API_BASE}/api/items",
        json={"name": f"Phase1 Item {suffix}", "barcode": barcode, "default_expiration_days": 5},
        headers=headers,
        timeout=10,
    )
    assert create_item.status_code == 201
    item_id = create_item.json()["id"]

    create_loc_1 = httpx.post(f"{API_BASE}/api/locations", json={"name": f"Phase1-Pantry-{suffix}"}, headers=headers, timeout=10)
    create_loc_2 = httpx.post(f"{API_BASE}/api/locations", json={"name": f"Phase1-Fridge-{suffix}"}, headers=headers, timeout=10)
    assert create_loc_1.status_code == 201
    assert create_loc_2.status_code == 201
    loc_1 = create_loc_1.json()["id"]
    loc_2 = create_loc_2.json()["id"]

    add = httpx.post(
        f"{API_BASE}/api/inventory/add",
        json={"item_id": item_id, "location_id": loc_1, "quantity": 10, "unit": "each"},
        headers=headers,
        timeout=10,
    )
    assert add.status_code == 200

    remove = httpx.post(
        f"{API_BASE}/api/inventory/remove",
        json={"item_id": item_id, "location_id": loc_1, "quantity": 3, "unit": "each", "reason": "used"},
        headers=headers,
        timeout=10,
    )
    assert remove.status_code == 200

    move = httpx.post(
        f"{API_BASE}/api/inventory/move",
        json={"item_id": item_id, "source_location_id": loc_1, "target_location_id": loc_2, "quantity": 4, "unit": "each"},
        headers=headers,
        timeout=10,
    )
    assert move.status_code == 200

    adjust = httpx.post(
        f"{API_BASE}/api/inventory/adjust",
        json={"item_id": item_id, "location_id": loc_2, "quantity": 2, "unit": "each"},
        headers=headers,
        timeout=10,
    )
    assert adjust.status_code == 200

    inv_1 = httpx.get(f"{API_BASE}/api/inventory?location_id={loc_1}", headers=headers, timeout=10)
    inv_2 = httpx.get(f"{API_BASE}/api/inventory?location_id={loc_2}", headers=headers, timeout=10)
    assert inv_1.status_code == 200
    assert inv_2.status_code == 200

    qty_1 = inv_1.json()["items"][0]["quantity"]
    qty_2 = inv_2.json()["items"][0]["quantity"]
    assert str(qty_1).startswith("3")
    assert str(qty_2).startswith("2")

    tx = httpx.get(f"{API_BASE}/api/inventory/transactions?limit=20", headers=headers, timeout=10)
    assert tx.status_code == 200
    assert len(tx.json()["transactions"]) >= 5
