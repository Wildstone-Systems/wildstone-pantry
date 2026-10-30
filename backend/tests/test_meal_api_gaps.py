"""Endpoints added in response to an agent's API critique.

The critique's premise ("PantryOS has no ingestion API") was wrong — plate/
calories were live — but three of its asks were real gaps:
  * logging a RECIPE portion, not just an item
  * aggregating over a date RANGE, not one day
  * saying whether a total is complete or an underestimate
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import httpx


def _admin(client: httpx.Client) -> dict[str, str]:
    client.post("/api/auth/bootstrap-admin", json={"email": "a@b.c", "password": "TestPass123!"})
    t = client.post("/api/auth/login", json={"email": "a@b.c", "password": "TestPass123!"}).json()["token"]
    return {"Authorization": f"Bearer {t}"}


def _profile(client, h, name="Alex") -> str:
    return client.post("/api/household/profiles", json={"display_name": name}, headers=h).json()["id"]


def test_openapi_is_reachable_under_the_proxied_api_prefix(client: httpx.Client) -> None:
    """The root cause of the whole critique: deployments proxy only /api, so a
    bare /openapi.json returns the frontend HTML and an agent discovers nothing."""
    spec = client.get("/api/openapi.json")
    assert spec.status_code == 200
    assert spec.headers["content-type"].startswith("application/json")
    paths = spec.json()["paths"]
    assert "/api/consumption/plate" in paths
    assert "/api/consumption/calories" in paths


def test_log_a_recipe_portion_without_touching_inventory(client: httpx.Client) -> None:
    h = _admin(client)
    profile = _profile(client, h)
    loc = client.post("/api/locations", json={"name": "Pantry"}, headers=h).json()["id"]
    client.post("/api/items", json={"name": "Oats", "energy_kcal_100g": 380, "serving_size_g": 100}, headers=h)
    recipe = client.post(
        "/api/recipes",
        json={"name": "Porridge", "servings": 4, "ingredients": [{"name": "Oats", "quantity": 400, "unit": "g"}]},
        headers=h,
    ).json()

    before = client.get(f"/api/inventory?location_id={loc}", headers=h).json()["items"]

    resp = client.post(
        "/api/consumption/plate-recipe",
        json={"recipe_id": recipe["id"], "profile_id": profile, "servings": 2, "meal_type": "dinner"},
        headers=h,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    # 400g oats @ 380/100g = 1520 total over 4 servings = 380/serving; 2 = 760.
    assert Decimal(str(body["calories"])) == Decimal("760")
    assert body["complete"] is True

    # Cooking deducts; eating a portion must NOT deduct again.
    assert client.get(f"/api/inventory?location_id={loc}", headers=h).json()["items"] == before

    today = datetime.now(UTC).date().isoformat()
    rollup = client.get(f"/api/consumption/calories?profile_id={profile}&on={today}", headers=h).json()
    assert Decimal(str(rollup["total_calories"])) == Decimal("760")
    assert rollup["entries"][0]["meal_type"] == "dinner"


def test_recipe_meal_flags_an_incomplete_total(client: httpx.Client) -> None:
    """An ingredient with no nutrition makes the figure a floor, not a total."""
    h = _admin(client)
    profile = _profile(client, h, "Sam")
    client.post("/api/items", json={"name": "Rice", "energy_kcal_100g": 360, "serving_size_g": 100}, headers=h)
    client.post("/api/items", json={"name": "Mystery Spice"}, headers=h)
    recipe = client.post(
        "/api/recipes",
        json={
            "name": "Plain Rice",
            "servings": 2,
            "ingredients": [
                {"name": "Rice", "quantity": 200, "unit": "g"},
                {"name": "Mystery Spice", "quantity": 5, "unit": "g"},
            ],
        },
        headers=h,
    ).json()

    body = client.post(
        "/api/consumption/plate-recipe",
        json={"recipe_id": recipe["id"], "profile_id": profile, "servings": 1},
        headers=h,
    ).json()
    assert body["complete"] is False


def test_nutrition_summary_aggregates_a_date_range(client: httpx.Client) -> None:
    h = _admin(client)
    profile = _profile(client, h, "Jo")
    item = client.post("/api/items", json={"name": "Bar", "calories_per_unit": 100}, headers=h).json()["id"]
    hp = {**h, "X-Household-Profile": profile}
    client.post("/api/consumption/plate", json={"item_id": item, "quantity": 3, "unit": "each"}, headers=hp)

    today = datetime.now(UTC).date()
    start = (today - timedelta(days=6)).isoformat()
    summary = client.get(
        f"/api/consumption/nutrition-summary?profile_id={profile}&from={start}&to={today.isoformat()}",
        headers=h,
    )
    assert summary.status_code == 200, summary.text
    data = summary.json()
    assert Decimal(str(data["total"]["calories"])) == Decimal("300")
    assert len(data["days"]) == 7  # empty days included, so a chart needs no gap-filling
    assert Decimal(str(data["days"][-1]["calories"])) == Decimal("300")
    # 300 over 7 days.
    assert Decimal(str(data["daily_average"]["calories"])).quantize(Decimal("0.01")) == Decimal("42.86")


def test_nutrition_summary_rejects_a_backwards_or_absurd_range(client: httpx.Client) -> None:
    h = _admin(client)
    profile = _profile(client, h, "Robin")
    assert client.get(
        f"/api/consumption/nutrition-summary?profile_id={profile}&from=2026-08-10&to=2026-08-01", headers=h
    ).status_code == 400
    assert client.get(
        f"/api/consumption/nutrition-summary?profile_id={profile}&from=2000-01-01&to=2026-08-01", headers=h
    ).status_code == 400


def test_calorie_rollup_reports_completeness(client: httpx.Client) -> None:
    h = _admin(client)
    profile = _profile(client, h, "Pat")
    good = client.post("/api/items", json={"name": "Apple", "calories_per_unit": 95}, headers=h).json()["id"]
    bad = client.post("/api/items", json={"name": "Leftovers"}, headers=h).json()["id"]
    hp = {**h, "X-Household-Profile": profile}
    today = datetime.now(UTC).date().isoformat()

    client.post("/api/consumption/plate", json={"item_id": good, "quantity": 1, "unit": "each"}, headers=hp)
    assert client.get(f"/api/consumption/calories?profile_id={profile}&on={today}", headers=h).json()["complete"] is True

    client.post("/api/consumption/plate", json={"item_id": bad, "quantity": 1, "unit": "each"}, headers=hp)
    assert client.get(f"/api/consumption/calories?profile_id={profile}&on={today}", headers=h).json()["complete"] is False
