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


def test_phase8_purchase_preview_makes_no_db_writes() -> None:
    headers = _auth_headers()
    suffix = uuid.uuid4().hex[:8]

    before_purchases = httpx.get(f"{API_BASE}/api/purchases?limit=200", headers=headers, timeout=10)
    assert before_purchases.status_code == 200
    before_count = len(before_purchases.json()["purchases"])

    before_items = httpx.get(f"{API_BASE}/api/items", headers=headers, timeout=10)
    assert before_items.status_code == 200
    before_item_count = len(before_items.json()["items"])

    location = httpx.post(f"{API_BASE}/api/locations", json={"name": f"Phase8-Loc-{suffix}"}, headers=headers, timeout=10)
    assert location.status_code == 201
    location_id = location.json()["id"]

    csv_text = f"name,quantity,unit\nMilk {suffix},2,each\nBread {suffix},1,each\n"

    preview = httpx.post(
        f"{API_BASE}/api/purchases/preview-csv",
        json={
            "location_id": location_id,
            "csv_text": csv_text,
            "merchant": "PreviewTest",
            "purchased_on": "2026-02-13",
            "create_missing_items": True,
            "apply_to_inventory": True,
            "default_unit": "each",
        },
        headers=headers,
        timeout=20,
    )
    assert preview.status_code == 200
    payload = preview.json()
    assert payload["line_count"] == 2

    after_purchases = httpx.get(f"{API_BASE}/api/purchases?limit=200", headers=headers, timeout=10)
    assert after_purchases.status_code == 200
    after_count = len(after_purchases.json()["purchases"])
    assert after_count == before_count

    after_items = httpx.get(f"{API_BASE}/api/items", headers=headers, timeout=10)
    assert after_items.status_code == 200
    after_item_count = len(after_items.json()["items"])
    assert after_item_count == before_item_count
