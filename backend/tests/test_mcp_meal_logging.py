"""Meal logging + calorie tools over the agent-key path.

The HTTP API had these all along, but the agent's TOOL surface didn't — an
agent asked "how many calories have I eaten?" concluded PantryOS had no
meal-logging API at all. These pin the client wiring end-to-end so the tools
can't silently drift away from the endpoints again.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app
from mcp_server.client import PantryOSClient


def _client() -> tuple[PantryOSClient, TestClient]:
    """The MCP client, plus a raw TestClient authenticated as the ADMIN.

    Setup runs as admin, not as the agent: creating household profiles is an
    admin-only action (the agent key can read profiles and log against them,
    but not create them). Bootstrapping the admin also turns auth on, so an
    unauthenticated setup POST would just 401.
    """
    c = TestClient(app)
    c.post("/api/auth/bootstrap-admin", json={"email": "admin@pantryos.local", "password": "TestPass123!"})
    token = c.post(
        "/api/auth/login", json={"email": "admin@pantryos.local", "password": "TestPass123!"}
    ).json()["token"]
    key = c.post(
        "/api/agent-keys", json={"name": "meals"}, headers={"Authorization": f"Bearer {token}"}
    ).json()["secret"]
    admin = TestClient(app)
    admin.headers.update({"Authorization": f"Bearer {token}"})
    return PantryOSClient(agent_key=key, http=TestClient(app)), admin


def test_agent_can_log_a_meal_and_read_calories_back() -> None:
    client, raw = _client()

    profile = raw.post(
        "/api/household/profiles", json={"display_name": "Alex", "daily_calorie_target": 2000}
    ).json()
    item = raw.post("/api/items", json={"name": "Banana", "calories_per_unit": 105}).json()

    # The agent discovers eaters, then logs against one.
    profiles = client.list_household_profiles()["profiles"]
    assert any(p["id"] == profile["id"] for p in profiles)

    logged = client.log_meal(item_id=item["id"], profile_id=profile["id"], quantity=2, unit="each")
    assert float(logged["calories"]) == 210
    assert logged["location_id"] is None  # off-pantry: nothing deducted

    rollup = client.calories_for(profile["id"], tz="America/New_York")
    assert float(rollup["total_calories"]) == 210
    assert float(rollup["target"]) == 2000
    assert len(rollup["entries"]) == 1

    summary = client.household_calories(tz="America/New_York")
    assert any(float(p["total_calories"]) == 210 for p in summary["profiles"])


def test_agent_can_undo_a_mistaken_meal() -> None:
    client, raw = _client()
    profile = raw.post("/api/household/profiles", json={"display_name": "Sam"}).json()
    item = raw.post("/api/items", json={"name": "Cracker", "calories_per_unit": 10}).json()

    logged = client.log_meal(item_id=item["id"], profile_id=profile["id"], quantity=1, unit="each")
    client.delete_meal(logged["event_id"])

    rollup = client.calories_for(profile["id"], tz="America/New_York")
    assert float(rollup["total_calories"]) == 0
    assert rollup["entries"] == []


def test_agent_logs_a_no_barcode_food_via_usda_search(monkeypatch) -> None:
    """The "three hard boiled eggs" path: search USDA, materialise an item,
    log against it — none of which needs the food to be in the catalog first."""
    from modules.catalog.services import barcodes as barcodes_service
    from modules.catalog.services.barcode_providers import NutritionEstimate

    monkeypatch.setattr(
        barcodes_service,
        "search_nutrition_candidates",
        lambda term, **k: [
            NutritionEstimate(
                nutrition={"energy-kcal_100g": 155, "proteins_100g": 12.6},
                source_description="EGG, WHOLE, COOKED, HARD-BOILED",
                fdc_id=173424,
                data_type="SR Legacy",
            )
        ],
    )
    monkeypatch.setattr(barcodes_service, "usda_food_portion_grams_per_each", lambda fdc_id, **k: None)

    client, raw = _client()
    profile = raw.post("/api/household/profiles", json={"display_name": "Jo"}).json()

    hits = client.search_food("hard boiled egg")["candidates"]
    assert hits[0]["fdc_id"] == 173424

    item = client.item_from_food_search(fdc_id=173424, query="hard boiled egg")
    logged = client.log_meal(item_id=item["id"], profile_id=profile["id"], quantity=150, unit="g")
    assert round(float(logged["calories"])) == round(155 * 1.5)


def test_agent_can_read_recipe_nutrition() -> None:
    client, raw = _client()
    raw.post("/api/items", json={"name": "Oats", "energy_kcal_100g": 380, "serving_size_g": 100})
    recipe = raw.post(
        "/api/recipes",
        json={"name": "Porridge", "servings": 2, "ingredients": [{"name": "Oats", "quantity": 200, "unit": "g"}]},
    ).json()

    nutrition = client.recipe_nutrition(recipe["id"])
    assert nutrition["servings"] == 2
    assert nutrition["total"]["calories"] == 760  # 380 per 100g * 200g
    assert nutrition["per_serving"]["calories"] == 380


def test_todays_calories_mean_today_in_the_callers_zone() -> None:
    """Regression: with no explicit date, "today" was the server's UTC day.

    Containers run UTC, so between 20:00 and midnight Eastern the UTC day is
    already tomorrow. Asking "what have I eaten today" at nine in the evening in
    New York therefore queried a day that had barely begun, and the whole
    evening's meals vanished — including from the agent's own view, which is
    where a silently empty answer does the most damage.

    Note this test only *fails* during that window, which is why it took a
    late-evening run to surface. The assertion is written against the zone, so
    it is meaningful at every hour.
    """
    from modules.inventory.services.consumption import today_in_zone

    client, raw = _client()
    profile = raw.post("/api/household/profiles", json={"display_name": "Zoned"}).json()
    item = raw.post("/api/items", json={"name": "Evening Banana", "calories_per_unit": 105}).json()

    client.log_meal(item_id=item["id"], profile_id=profile["id"], quantity=2, unit="each")

    rollup = client.calories_for(profile["id"], tz="America/New_York")
    assert rollup["date"] == str(today_in_zone("America/New_York"))
    assert float(rollup["total_calories"]) == 210, "an evening meal must not fall off the day"

    summary = client.household_calories(tz="America/New_York")
    assert summary["date"] == str(today_in_zone("America/New_York"))
