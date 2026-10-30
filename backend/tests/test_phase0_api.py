from __future__ import annotations

import os
import uuid

import httpx


API_BASE = os.getenv("API_BASE_URL", "http://localhost:8000")


def _auth_headers() -> dict[str, str]:
    email = os.getenv("TEST_ADMIN_EMAIL", "admin@pantryos.local")
    password = os.getenv("TEST_ADMIN_PASSWORD", "TestPass123!")
    # Bootstrap only works on a brand-new DB. If users already exist, it returns 409.
    httpx.post(f"{API_BASE}/api/auth/bootstrap-admin", json={"email": email, "password": password}, timeout=10)
    login = httpx.post(f"{API_BASE}/api/auth/login", json={"email": email, "password": password}, timeout=10)
    assert login.status_code == 200
    headers = {"Authorization": f"Bearer {login.json()['token']}"}
    return headers


def test_health() -> None:
    response = httpx.get(f"{API_BASE}/health", timeout=10)
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert payload["database"] == "connected"


def test_item_crud() -> None:
    headers = _auth_headers()
    # Barcodes are digits; keep tests aligned with barcode normalization rules.
    barcode = str(uuid.uuid4().int)[:12]

    create = httpx.post(
        f"{API_BASE}/api/items",
        json={"name": "Pytest Item", "barcode": barcode, "default_expiration_days": 5},
        headers=headers,
        timeout=10,
    )
    assert create.status_code == 201
    item = create.json()
    item_id = item["id"]

    fetch = httpx.get(f"{API_BASE}/api/items/{item_id}", headers=headers, timeout=10)
    assert fetch.status_code == 200
    assert fetch.json()["barcode"] == barcode

    update = httpx.put(
        f"{API_BASE}/api/items/{item_id}",
        json={"name": "Pytest Item Updated", "default_expiration_days": 10},
        headers=headers,
        timeout=10,
    )
    assert update.status_code == 200
    assert update.json()["name"] == "Pytest Item Updated"

    # DELETE archives rather than destroys — it used to cascade into the event
    # log and take the item's history with it. See test_item_archive_purge.py.
    delete = httpx.delete(f"{API_BASE}/api/items/{item_id}", headers=headers, timeout=10)
    assert delete.status_code == 200
    assert delete.json()["is_active"] is False

    # Archived items drop out of the catalogue...
    listed = httpx.get(f"{API_BASE}/api/items", headers=headers, timeout=10).json()["items"]
    assert all(row["id"] != item_id for row in listed)

    # ...but the row is still there, and a purge is what actually removes it.
    purge = httpx.request(
        "DELETE",
        f"{API_BASE}/api/items/{item_id}/purge",
        params={"confirm_name": "Pytest Item Updated"},
        headers=headers,
        timeout=10,
    )
    assert purge.status_code == 200, purge.text

    missing = httpx.get(f"{API_BASE}/api/items/{item_id}", headers=headers, timeout=10)
    assert missing.status_code == 404
