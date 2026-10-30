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


def test_phase4_insights_endpoints() -> None:
    headers = _auth_headers()
    suffix = uuid.uuid4().hex[:8]

    location = httpx.post(f"{API_BASE}/api/locations", json={"name": f"Phase4-Loc-{suffix}"}, headers=headers, timeout=10)
    assert location.status_code == 201
    location_id = location.json()["id"]

    item = httpx.post(
        f"{API_BASE}/api/items",
        json={"name": f"TrendItem {suffix}", "default_expiration_days": 3},
        headers=headers,
        timeout=10,
    )
    assert item.status_code == 201
    item_id = item.json()["id"]

    httpx.post(f"{API_BASE}/api/inventory/add", json={"item_id": item_id, "location_id": location_id, "quantity": 10, "unit": "each"}, headers=headers, timeout=10)
    httpx.post(f"{API_BASE}/api/inventory/remove", json={"item_id": item_id, "location_id": location_id, "quantity": 6, "unit": "each", "reason": "used"}, headers=headers, timeout=10)
    httpx.post(
        f"{API_BASE}/api/inventory/adjust",
        json={"item_id": item_id, "location_id": location_id, "quantity": 2, "unit": "each", "expiration_date": "2026-02-15"},
        headers=headers,
        timeout=10,
    )

    trends = httpx.get(f"{API_BASE}/api/insights/trends/{location_id}", headers=headers, timeout=10)
    assert trends.status_code == 200

    restock = httpx.get(f"{API_BASE}/api/insights/restock/{location_id}", headers=headers, timeout=10)
    assert restock.status_code == 200

    expiring = httpx.get(f"{API_BASE}/api/insights/expiring/{location_id}?within_days=365", headers=headers, timeout=10)
    assert expiring.status_code == 200
