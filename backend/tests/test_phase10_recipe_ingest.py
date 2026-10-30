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


def test_phase10_ingest_parsed_recipe_creates_recipe_and_ingredients() -> None:
    headers = _auth_headers()
    suffix = uuid.uuid4().hex[:8]

    resp = httpx.post(
        f"{API_BASE}/api/recipes/ingest-parsed",
        json={
            "title": f"Phase10 Test Recipe {suffix}",
            "instructions": "Mix and cook.",
            "ingredients": [
                {"raw_line": "1 cup flour", "name": f"Flour {suffix}", "quantity": 1, "unit": "cup"},
                {"raw_line": "2 eggs", "name": f"Eggs {suffix}", "quantity": 2, "unit": "each"},
            ],
            "source": "text",
            "source_filename": None,
            "source_text": "dummy",
            "create_missing_items": True,
        },
        headers=headers,
        timeout=20,
    )
    assert resp.status_code == 200
    recipe_id = resp.json()["recipe_id"]

    detail = httpx.get(f"{API_BASE}/api/recipes/{recipe_id}", headers=headers, timeout=10)
    assert detail.status_code == 200
    payload = detail.json()
    assert payload["recipe"]["id"] == recipe_id
    assert len(payload["ingredients"]) == 2
