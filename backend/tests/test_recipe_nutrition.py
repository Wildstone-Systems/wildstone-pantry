"""Recipe nutrition rollup + plate macro snapshot (step 5 / ADR 0011 D3-D4)."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import httpx


def _admin_headers(client: httpx.Client) -> dict[str, str]:
    client.post("/api/auth/bootstrap-admin", json={"email": "admin@pantryos.local", "password": "TestPass123!"})
    login = client.post("/api/auth/login", json={"email": "admin@pantryos.local", "password": "TestPass123!"})
    return {"Authorization": f"Bearer {login.json()['token']}"}


def _item(client, h, **fields) -> str:
    resp = client.post("/api/items", json=fields, headers=h)
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def _recipe(client, h, *, name: str, servings: int = 1) -> str:
    resp = client.post("/api/recipes", json={"name": name, "servings": servings}, headers=h)
    assert resp.status_code == 200, resp.text
    assert resp.json()["servings"] == servings
    return resp.json()["id"]


def _set_ingredients(client, h, recipe_id, ingredients) -> None:
    resp = client.put(f"/api/recipes/{recipe_id}/ingredients", json=ingredients, headers=h)
    assert resp.status_code == 200, resp.text


def test_recipe_nutrition_total_and_per_serving(client: httpx.Client) -> None:
    h = _admin_headers(client)
    # Flour: 364 kcal / 10g protein per 100g. 200 g used → 728 kcal, 20 g protein.
    flour = _item(
        client, h, name="Flour", energy_kcal_100g=364, protein_g_100g=10, fat_g_100g=1, carbs_g_100g=76
    )
    recipe = _recipe(client, h, name="Simple Bread", servings=4)
    _set_ingredients(client, h, recipe, [{"item_id": flour, "quantity_required": 200, "unit": "g"}])

    resp = client.get(f"/api/recipes/{recipe}/nutrition", headers=h)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["complete"] is True
    assert body["unresolved"] == []
    assert body["total"]["calories"] == 728.0
    assert body["total"]["protein_g"] == 20.0
    assert body["per_serving"]["calories"] == 182.0  # 728 / 4
    assert body["per_serving"]["protein_g"] == 5.0


def test_recipe_nutrition_marks_partial_when_unconvertible(client: httpx.Client) -> None:
    h = _admin_headers(client)
    # Volume amount but no density → unconvertible → partial.
    mystery = _item(client, h, name="Mystery Spice", energy_kcal_100g=250)
    flour = _item(client, h, name="Flour2", energy_kcal_100g=364, protein_g_100g=10)
    recipe = _recipe(client, h, name="Partial Recipe", servings=1)
    _set_ingredients(
        client,
        h,
        recipe,
        [
            {"item_id": flour, "quantity_required": 100, "unit": "g"},
            {"item_id": mystery, "quantity_required": 1, "unit": "cup"},
        ],
    )

    body = client.get(f"/api/recipes/{recipe}/nutrition", headers=h).json()
    assert body["complete"] is False
    assert len(body["unresolved"]) == 1
    assert body["unresolved"][0]["item_name"] == "Mystery Spice"
    # Flour still contributes; the mystery spice is excluded, not zeroed wrong.
    assert body["total"]["calories"] == 364.0


def test_recipe_nutrition_reflects_edits(client: httpx.Client) -> None:
    h = _admin_headers(client)
    item = _item(client, h, name="EditMe", energy_kcal_100g=100)
    recipe = _recipe(client, h, name="EditRecipe")
    _set_ingredients(client, h, recipe, [{"item_id": item, "quantity_required": 100, "unit": "g"}])
    assert client.get(f"/api/recipes/{recipe}/nutrition", headers=h).json()["total"]["calories"] == 100.0

    client.put(f"/api/items/{item}", json={"name": "EditMe", "energy_kcal_100g": 200}, headers=h)
    assert client.get(f"/api/recipes/{recipe}/nutrition", headers=h).json()["total"]["calories"] == 200.0


def test_recipe_nutrition_no_macro_data_is_unresolved_not_complete(client: httpx.Client) -> None:
    """A mass-convertible ingredient with no per-100g data contributes nothing —
    it must be reported unresolved, not as a complete rollup of null totals."""
    h = _admin_headers(client)
    plain = _item(client, h, name="Plain Salt")  # grams unit converts, but no macros
    recipe = _recipe(client, h, name="Just Salt", servings=1)
    _set_ingredients(client, h, recipe, [{"item_id": plain, "quantity_required": 5, "unit": "g"}])

    body = client.get(f"/api/recipes/{recipe}/nutrition", headers=h).json()
    assert body["complete"] is False
    assert len(body["unresolved"]) == 1
    assert body["total"]["calories"] is None


def test_recipe_nutrition_404(client: httpx.Client) -> None:
    h = _admin_headers(client)
    resp = client.get("/api/recipes/00000000-0000-0000-0000-000000000000/nutrition", headers=h)
    assert resp.status_code == 404


# --- plate macro snapshot --------------------------------------------------


def _profile(client, h, *, name: str, target: int | None = None) -> str:
    body: dict = {"display_name": name}
    if target is not None:
        body["daily_calorie_target"] = target
    return client.post("/api/household/profiles", json=body, headers=h).json()["id"]


def _location(client, h) -> str:
    return client.post("/api/locations", json={"name": "Pantry"}, headers=h).json()["id"]


def _stock(client, h, item_id, loc, qty, unit="each") -> None:
    resp = client.post(
        "/api/inventory/add",
        json={"item_id": item_id, "location_id": loc, "quantity": qty, "unit": unit},
        headers=h,
    )
    assert resp.status_code == 200, resp.text


def test_plate_snapshots_macros_and_rollup_sums_them(client: httpx.Client) -> None:
    h = _admin_headers(client)
    profile = _profile(client, h, name="Alex", target=2000)
    loc = _location(client, h)
    # Chicken: per-100g macros + grams_per_each so an "each" plate converts.
    chicken = _item(
        client,
        h,
        name="Chicken Breast",
        energy_kcal_100g=165,
        protein_g_100g=31,
        fat_g_100g=3.6,
        carbs_g_100g=0,
        grams_per_each=100,
    )
    _stock(client, h, chicken, loc, 5)

    plated = client.post(
        "/api/consumption/plate",
        json={"item_id": chicken, "location_id": loc, "quantity": 2, "unit": "each", "profile_id": profile},
        headers=h,
    )
    assert plated.status_code == 200, plated.text

    rollup = client.get(f"/api/consumption/calories?profile_id={profile}&on={datetime.now(UTC).date()}", headers=h).json()
    # 2 each * 100g = 200g → 2x per-100g values.
    assert Decimal(str(rollup["total_calories"])) == Decimal("330.00")
    assert Decimal(str(rollup["total_protein_g"])) == Decimal("62.00")
    assert Decimal(str(rollup["total_fat_g"])) == Decimal("7.20")
    entry = rollup["entries"][0]
    assert Decimal(str(entry["protein_g"])) == Decimal("62.00")


def test_plate_prefers_structured_calories_over_stale_legacy(client: httpx.Client) -> None:
    """When an amount is convertible and per-100g energy exists, the snapshot uses
    it (consistent with the macros), not a stale legacy calories_per_unit."""
    h = _admin_headers(client)
    profile = _profile(client, h, name="Jo", target=2000)
    loc = _location(client, h)
    # Stale 95 kcal/unit vs structured 165 kcal/100g with grams_per_each=100.
    item = _item(
        client,
        h,
        name="Chicken (mixed)",
        calories_per_unit=95,
        energy_kcal_100g=165,
        protein_g_100g=31,
        grams_per_each=100,
    )
    _stock(client, h, item, loc, 3)
    client.post(
        "/api/consumption/plate",
        json={"item_id": item, "location_id": loc, "quantity": 1, "unit": "each", "profile_id": profile},
        headers=h,
    )
    rollup = client.get(f"/api/consumption/calories?profile_id={profile}&on={datetime.now(UTC).date()}", headers=h).json()
    # 1 each * 100g -> 165 kcal (structured), not the stale 95.
    assert Decimal(str(rollup["total_calories"])) == Decimal("165.00")
    assert Decimal(str(rollup["total_protein_g"])) == Decimal("31.00")


def test_plate_without_per100g_still_works_macros_null(client: httpx.Client) -> None:
    h = _admin_headers(client)
    profile = _profile(client, h, name="Sam", target=1800)
    loc = _location(client, h)
    # Legacy item: only calories_per_unit, no per-100g macros.
    apple = _item(client, h, name="Apple", calories_per_unit=95)
    _stock(client, h, apple, loc, 3)

    plated = client.post(
        "/api/consumption/plate",
        json={"item_id": apple, "location_id": loc, "quantity": 1, "unit": "each", "profile_id": profile},
        headers=h,
    )
    assert plated.status_code == 200, plated.text
    rollup = client.get(f"/api/consumption/calories?profile_id={profile}&on={datetime.now(UTC).date()}", headers=h).json()
    assert Decimal(str(rollup["total_calories"])) == Decimal("95.00")
    assert rollup["total_protein_g"] is None
