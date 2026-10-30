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


def test_phase3_recipe_cook_and_suggestions() -> None:
    headers = _auth_headers()
    suffix = uuid.uuid4().hex[:8]

    location = httpx.post(f"{API_BASE}/api/locations", json={"name": f"Phase3-Loc-{suffix}"}, headers=headers, timeout=10)
    assert location.status_code == 201
    location_id = location.json()["id"]

    egg = httpx.post(f"{API_BASE}/api/items", json={"name": f"Eggs {suffix}"}, headers=headers, timeout=10)
    milk = httpx.post(f"{API_BASE}/api/items", json={"name": f"Milk {suffix}"}, headers=headers, timeout=10)
    assert egg.status_code == 201
    assert milk.status_code == 201
    egg_id = egg.json()["id"]
    milk_id = milk.json()["id"]

    httpx.post(f"{API_BASE}/api/inventory/add", json={"item_id": egg_id, "location_id": location_id, "quantity": 6, "unit": "each"}, headers=headers, timeout=10)
    httpx.post(f"{API_BASE}/api/inventory/add", json={"item_id": milk_id, "location_id": location_id, "quantity": 2, "unit": "each"}, headers=headers, timeout=10)

    recipe = httpx.post(
        f"{API_BASE}/api/recipes",
        json={"name": f"Omelette {suffix}", "instructions": "Cook quickly"},
        headers=headers,
        timeout=10,
    )
    assert recipe.status_code == 200
    recipe_id = recipe.json()["id"]

    ingredients = httpx.put(
        f"{API_BASE}/api/recipes/{recipe_id}/ingredients",
        json=[
            {"item_id": egg_id, "quantity_required": 2, "unit": "each"},
            {"item_id": milk_id, "quantity_required": 1, "unit": "each"},
        ],
        headers=headers,
        timeout=10,
    )
    assert ingredients.status_code == 200

    suggestions = httpx.get(f"{API_BASE}/api/recipes/suggestions/{location_id}", headers=headers, timeout=10)
    assert suggestions.status_code == 200

    cook = httpx.post(
        f"{API_BASE}/api/recipes/{recipe_id}/cook",
        json={"location_id": location_id, "servings": 1, "leftover_servings": 0},
        headers=headers,
        timeout=10,
    )
    assert cook.status_code == 200

    inventory = httpx.get(f"{API_BASE}/api/inventory?location_id={location_id}", headers=headers, timeout=10)
    assert inventory.status_code == 200
    rows = {row["item_name"]: row["quantity"] for row in inventory.json()["items"]}
    assert str(rows[f"Eggs {suffix}"]).startswith("4")
