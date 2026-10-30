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


def test_phase7_purchase_import_is_deduped() -> None:
    headers = _auth_headers()
    suffix = uuid.uuid4().hex[:8]

    location = httpx.post(f"{API_BASE}/api/locations", json={"name": f"Phase7-Loc-{suffix}"}, headers=headers, timeout=10)
    assert location.status_code == 201
    location_id = location.json()["id"]

    csv_text = f"name,quantity,unit\nMilk {suffix},2,each\nBread {suffix},1,each\n"

    first = httpx.post(
        f"{API_BASE}/api/purchases/import-csv",
        json={"location_id": location_id, "csv_text": csv_text, "merchant": "Test", "purchased_on": "2026-02-13"},
        headers=headers,
        timeout=20,
    )
    assert first.status_code == 200

    duplicate = httpx.post(
        f"{API_BASE}/api/purchases/import-csv",
        json={"location_id": location_id, "csv_text": csv_text, "merchant": "Test", "purchased_on": "2026-02-13"},
        headers=headers,
        timeout=20,
    )
    assert duplicate.status_code == 409
