"""Nutrition forecast (#34): planned calories per person per day vs target."""

from __future__ import annotations

import httpx
import pytest

SUNDAY = "2026-11-01"
MONDAY = "2026-11-02"


def _admin(client: httpx.Client) -> dict[str, str]:
    client.post("/api/auth/bootstrap-admin", json={"email": "admin@pantryos.local", "password": "TestPass123!"})
    login = client.post("/api/auth/login", json={"email": "admin@pantryos.local", "password": "TestPass123!"})
    return {"Authorization": f"Bearer {login.json()['token']}"}


@pytest.fixture
def household(client: httpx.Client) -> dict:
    """Alex (target 2000) and Sam (no target); Porridge serves 2 at 200 kcal each."""
    h = _admin(client)
    alex = client.post("/api/household/profiles", json={"display_name": "Alex", "daily_calorie_target": 2000}, headers=h)
    sam = client.post("/api/household/profiles", json={"display_name": "Sam"}, headers=h)
    oats = client.post(
        "/api/items",
        json={"name": "Oats", "stock_mode": "measured", "size_quantity": 1, "size_unit": "kg", "energy_kcal_100g": 400},
        headers=h,
    ).json()["id"]
    porridge = client.post("/api/recipes", json={"name": "Porridge", "servings": 2}, headers=h).json()["id"]
    put = client.put(
        f"/api/recipes/{porridge}/ingredients",
        json=[{"item_id": oats, "quantity_required": 100, "unit": "g"}],
        headers=h,
    )
    assert put.status_code in (200, 201), put.text
    return {"h": h, "alex": alex.json()["id"], "sam": sam.json()["id"], "porridge": porridge}


def _plan(client: httpx.Client, **body) -> dict:
    body.setdefault("plan_date", MONDAY)
    body.setdefault("meal_type", "breakfast")
    resp = client.post("/api/meal-plan/entries", json=body)
    assert resp.status_code == 201, resp.text
    return resp.json()


def _day(client: httpx.Client, name: str, day: str = MONDAY) -> dict:
    resp = client.get("/api/meal-plan/nutrition", params={"start": SUNDAY})
    assert resp.status_code == 200, resp.text
    person = next(p for p in resp.json()["people"] if p["display_name"] == name)
    return {"target": person["target"], **next(d for d in person["days"] if d["date"] == day)}


def test_a_household_meal_is_shared_and_shown_against_targets(client: httpx.Client, household: dict) -> None:
    _plan(client, recipe_id=household["porridge"], servings=4)  # 4 × 200 kcal, shared by 2
    alex, sam = _day(client, "Alex"), _day(client, "Sam")
    assert (alex["calories"], alex["target"], alex["meals"]) == (400, 2000, 1)
    assert (sam["calories"], sam["target"]) == (400, None)
    assert _day(client, "Alex", SUNDAY)["calories"] == 0


def test_a_meal_for_one_person_is_all_theirs(client: httpx.Client, household: dict) -> None:
    _plan(client, recipe_id=household["porridge"], servings=1, profile_id=household["sam"])
    assert _day(client, "Sam")["calories"] == 200
    assert (_day(client, "Alex")["calories"], _day(client, "Alex")["meals"]) == (0, 0)


def test_an_item_meal_uses_its_calories_per_each(client: httpx.Client, household: dict) -> None:
    bar = client.post("/api/items", json={"name": "Protein bar", "calories_per_unit": 150}, headers=household["h"]).json()
    _plan(client, item_id=bar["id"], servings=2, meal_type="snack", profile_id=household["alex"])
    assert _day(client, "Alex")["calories"] == 300


def test_free_text_is_unknown_not_zero(client: httpx.Client, household: dict) -> None:
    _plan(client, title="Takeout", meal_type="dinner")
    alex = _day(client, "Alex")
    assert (alex["calories"], alex["unknown_meals"], alex["meals"]) == (0, 1, 1)


def test_skipped_meals_do_not_count(client: httpx.Client, household: dict) -> None:
    entry = _plan(client, recipe_id=household["porridge"], servings=2)
    client.post(f"/api/meal-plan/entries/{entry['id']}/skip")
    assert _day(client, "Alex")["meals"] == 0


def test_missing_ingredient_data_marks_the_day_partial(client: httpx.Client, household: dict) -> None:
    h = household["h"]
    mystery = client.post(
        "/api/items", json={"name": "Mystery", "stock_mode": "measured", "size_quantity": 1, "size_unit": "kg"}, headers=h
    ).json()["id"]
    oats = client.get("/api/recipes/" + household["porridge"], headers=h).json()["ingredients"][0]["item_id"]
    stew = client.post("/api/recipes", json={"name": "Stew", "servings": 1}, headers=h).json()["id"]
    client.put(
        f"/api/recipes/{stew}/ingredients",
        json=[
            {"item_id": oats, "quantity_required": 100, "unit": "g"},
            {"item_id": mystery, "quantity_required": 100, "unit": "g"},
        ],
        headers=h,
    )
    _plan(client, recipe_id=stew, servings=1, profile_id=household["alex"], meal_type="dinner")
    alex = _day(client, "Alex")
    assert (alex["calories"], alex["partial"]) == (400, True)


def test_forecast_needs_a_start(client: httpx.Client) -> None:
    assert client.get("/api/meal-plan/nutrition").status_code == 422
    assert client.get("/api/meal-plan/nutrition", params={"start": SUNDAY, "days": 0}).status_code == 422
