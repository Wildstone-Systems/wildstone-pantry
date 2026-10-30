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


def test_phase11_recipe_parse_image_invalid_returns_400() -> None:
    headers = _auth_headers()

    files = {"file": ("not-an-image.png", b"nope", "image/png")}
    resp = httpx.post(f"{API_BASE}/api/recipes/parse-image", headers=headers, files=files, timeout=20)
    assert resp.status_code == 400


def test_phase11_receipt_ingest_image_invalid_returns_400() -> None:
    headers = _auth_headers()
    suffix = uuid.uuid4().hex[:8]

    loc = httpx.post(f"{API_BASE}/api/locations", json={"name": f"Phase11-Loc-{suffix}"}, headers=headers, timeout=10)
    assert loc.status_code == 201
    location_id = loc.json()["id"]

    files = {"file": ("not-an-image.png", b"nope", "image/png")}
    resp = httpx.post(
        f"{API_BASE}/api/receipts/ingest-image",
        headers=headers,
        files=files,
        params={"location_id": location_id},
        timeout=20,
    )
    assert resp.status_code == 400
