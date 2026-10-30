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


def test_phase2_barcode_and_receipt_ingestion() -> None:
    headers = _auth_headers()
    suffix = uuid.uuid4().hex[:8]

    create_location = httpx.post(f"{API_BASE}/api/locations", json={"name": f"Phase2-Loc-{suffix}"}, headers=headers, timeout=10)
    assert create_location.status_code == 201
    location_id = create_location.json()["id"]

    create_item = httpx.post(
        f"{API_BASE}/api/items",
        json={"name": f"Milk {suffix}", "barcode": f"12345678{suffix[:4]}", "default_expiration_days": 7},
        headers=headers,
        timeout=10,
    )
    assert create_item.status_code == 201
    item_id = create_item.json()["id"]

    barcode_lookup = httpx.get(f"{API_BASE}/api/barcodes/lookup/12345678{suffix[:4]}", headers=headers, timeout=10)
    assert barcode_lookup.status_code == 200
    assert barcode_lookup.json()["found"] is True
    assert barcode_lookup.json()["item_id"] == item_id

    alias = httpx.post(
        f"{API_BASE}/api/barcodes/alias",
        json={"barcode": f"ALT-{suffix}", "item_id": item_id},
        headers=headers,
        timeout=10,
    )
    assert alias.status_code == 200

    alias_lookup = httpx.get(f"{API_BASE}/api/barcodes/lookup/ALT-{suffix}", headers=headers, timeout=10)
    assert alias_lookup.status_code == 200
    assert alias_lookup.json()["found"] is True

    receipt = httpx.post(
        f"{API_BASE}/api/receipts/ingest",
        json={
            "location_id": location_id,
            "raw_text": f"Milk {suffix},2,each\nBread {suffix},1,each",
            "source_filename": "phase2.txt",
        },
        headers=headers,
        timeout=20,
    )
    assert receipt.status_code == 200
    payload = receipt.json()
    assert payload["line_count"] >= 2

    inventory = httpx.get(f"{API_BASE}/api/inventory?location_id={location_id}", headers=headers, timeout=10)
    assert inventory.status_code == 200
    names = [row["item_name"] for row in inventory.json()["items"]]
    assert any(name.startswith("Milk") for name in names)
    assert any(name.startswith("Bread") for name in names)
