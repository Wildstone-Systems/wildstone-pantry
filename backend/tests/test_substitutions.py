"""Ingredient substitutions: learn-at-cook, candidates, suggestions (ADR 0011 D5)."""

from __future__ import annotations

from decimal import Decimal

import httpx


def _admin_headers(client: httpx.Client) -> dict[str, str]:
    client.post("/api/auth/bootstrap-admin", json={"email": "admin@pantryos.local", "password": "TestPass123!"})
    login = client.post("/api/auth/login", json={"email": "admin@pantryos.local", "password": "TestPass123!"})
    return {"Authorization": f"Bearer {login.json()['token']}"}


def _item(client, h, name) -> str:
    return client.post("/api/items", json={"name": name}, headers=h).json()["id"]


def _recipe(client, h, name) -> str:
    return client.post("/api/recipes", json={"name": name, "servings": 1}, headers=h).json()["id"]


def _set_ingredients(client, h, recipe_id, ingredients) -> None:
    resp = client.put(f"/api/recipes/{recipe_id}/ingredients", json=ingredients, headers=h)
    assert resp.status_code == 200, resp.text


def _location(client, h) -> str:
    return client.post("/api/locations", json={"name": "Pantry"}, headers=h).json()["id"]


def _stock(client, h, item_id, loc, qty, unit="cup") -> None:
    resp = client.post(
        "/api/inventory/add",
        json={"item_id": item_id, "location_id": loc, "quantity": qty, "unit": unit},
        headers=h,
    )
    assert resp.status_code == 200, resp.text


def _balance(client, h, item_id, loc) -> Decimal:
    resp = client.get(f"/api/inventory?location_id={loc}", headers=h)
    for row in resp.json()["items"]:
        if str(row["item_id"]) == str(item_id):
            return Decimal(str(row["quantity"]))
    return Decimal("0")


def test_cook_with_substitution_learns_and_decrements_substitute(client: httpx.Client) -> None:
    h = _admin_headers(client)
    loc = _location(client, h)
    butter = _item(client, h, "Butter")
    oil = _item(client, h, "Olive Oil")
    recipe = _recipe(client, h, "Cake")
    _set_ingredients(client, h, recipe, [{"item_id": butter, "quantity_required": 1, "unit": "cup"}])

    # Out of butter, stocked on oil.
    _stock(client, h, oil, loc, 5, unit="cup")

    cook = client.post(
        f"/api/recipes/{recipe}/cook",
        json={
            "location_id": loc,
            "servings": 2,
            "substitutions": [
                {"original_item_id": butter, "substitute_item_id": oil, "ratio": 0.75}
            ],
        },
        headers=h,
    )
    assert cook.status_code == 200, cook.text

    # Oil decremented by 1 * 2 servings * 0.75 = 1.5 → 5 - 1.5 = 3.5. Butter untouched.
    assert _balance(client, h, oil, loc) == Decimal("3.5")
    assert _balance(client, h, butter, loc) == Decimal("0")

    # The swap was learned.
    subs = client.get(f"/api/substitutions/for/{butter}", headers=h).json()["substitutions"]
    assert len(subs) == 1
    assert str(subs[0]["to_item_id"]) == str(oil)
    assert Decimal(str(subs[0]["ratio"])) == Decimal("0.75")


def test_suggestions_not_available_on_token_substitute_balance(client: httpx.Client) -> None:
    """A recipe isn't scored makeable when the substitute balance can't cover the
    required substituted amount."""
    h = _admin_headers(client)
    loc = _location(client, h)
    butter = _item(client, h, "Butter")
    oil = _item(client, h, "Oil")
    recipe = _recipe(client, h, "Cake")
    _set_ingredients(client, h, recipe, [{"item_id": butter, "quantity_required": 2, "unit": "cup"}])
    client.post(
        "/api/substitutions",
        json={"from_item_id": butter, "to_item_id": oil, "ratio": 0.75},
        headers=h,
    )
    _stock(client, h, oil, loc, 1, unit="cup")  # short of 1.5

    suggestions = client.get(f"/api/recipes/suggestions/{loc}", headers=h).json()["suggestions"]
    match = next(s for s in suggestions if str(s["recipe_id"]) == str(recipe))
    assert match["available_score"] == 0.0
    assert "Butter" in match["missing_items"]
    assert match["substitutions_used"] == []


def test_suggestions_keep_recipe_with_learned_substitute(client: httpx.Client) -> None:
    h = _admin_headers(client)
    loc = _location(client, h)
    butter = _item(client, h, "Butter")
    oil = _item(client, h, "Oil")
    recipe = _recipe(client, h, "Cake")
    _set_ingredients(client, h, recipe, [{"item_id": butter, "quantity_required": 1, "unit": "cup"}])

    # No butter; register + stock oil.
    client.post(
        "/api/substitutions",
        json={"from_item_id": butter, "to_item_id": oil, "ratio": 1, "outcome": "acceptable"},
        headers=h,
    )
    _stock(client, h, oil, loc, 3, unit="cup")

    suggestions = client.get(f"/api/recipes/suggestions/{loc}", headers=h).json()["suggestions"]
    match = next(s for s in suggestions if str(s["recipe_id"]) == str(recipe))
    assert match["available_score"] == 1.0  # covered by substitute
    assert match["missing_items"] == []
    assert len(match["substitutions_used"]) == 1
    assert match["substitutions_used"][0]["substitute_item"] == "Oil"


def test_suggestions_still_report_uncovered_missing(client: httpx.Client) -> None:
    h = _admin_headers(client)
    loc = _location(client, h)
    butter = _item(client, h, "Butter")
    recipe = _recipe(client, h, "Cake")
    _set_ingredients(client, h, recipe, [{"item_id": butter, "quantity_required": 1, "unit": "cup"}])

    suggestions = client.get(f"/api/recipes/suggestions/{loc}", headers=h).json()["suggestions"]
    match = next(s for s in suggestions if str(s["recipe_id"]) == str(recipe))
    assert match["available_score"] == 0.0
    assert "Butter" in match["missing_items"]


def test_self_substitution_rejected(client: httpx.Client) -> None:
    h = _admin_headers(client)
    butter = _item(client, h, "Butter")
    resp = client.post(
        "/api/substitutions",
        json={"from_item_id": butter, "to_item_id": butter},
        headers=h,
    )
    assert resp.status_code == 400
