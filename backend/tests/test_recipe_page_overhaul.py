"""Recipe list/delete, all-location suggestions, and FEFO-across-locations cooking.

Covers the gaps found while using the recipes page on a real household box: the
page could not list or delete recipes, and both suggestions and cooking were
pinned to a single location even though a meal's ingredients are normally spread
across the kitchen, a garage shelf and a freezer.
"""

from __future__ import annotations

import httpx
import pytest

from app.core.config import settings
from modules.automation.recipe_import_parser import parse_recipe_text


@pytest.fixture(autouse=True)
def _personal_mode(monkeypatch) -> None:
    """Seeding stock goes through the inventory API, which is still token-gated.

    These tests are about recipe behaviour, not about auth, so they run in
    personal mode rather than threading a login through every fixture.
    """

    monkeypatch.setattr(settings, "auth_enabled", False)


def _make_item(client: httpx.Client, name: str) -> str:
    resp = client.post("/api/items", json={"name": name})
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def _make_location(client: httpx.Client, name: str) -> str:
    resp = client.post("/api/locations", json={"name": name})
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def _add_stock(client: httpx.Client, *, item_id: str, location_id: str, quantity: float) -> None:
    resp = client.post(
        "/api/inventory/add",
        json={"item_id": item_id, "location_id": location_id, "quantity": quantity, "unit": "each"},
    )
    assert resp.status_code in {200, 201}, resp.text


def _make_recipe(client: httpx.Client, name: str, ingredients: list[dict]) -> str:
    resp = client.post("/api/recipes", json={"name": name})
    assert resp.status_code == 200, resp.text
    recipe_id = resp.json()["id"]
    if ingredients:
        saved = client.put(f"/api/recipes/{recipe_id}/ingredients", json=ingredients)
        assert saved.status_code == 200, saved.text
    return recipe_id


def test_recipe_list_reports_ingredient_count(client: httpx.Client) -> None:
    flour = _make_item(client, "Flour")
    _make_recipe(client, "Empty Recipe", [])
    _make_recipe(client, "Bread", [{"item_id": flour, "quantity_required": 2, "unit": "cup"}])

    rows = {row["name"]: row for row in client.get("/api/recipes").json()}
    assert rows["Bread"]["ingredient_count"] == 1
    assert rows["Empty Recipe"]["ingredient_count"] == 0


def test_recipe_can_be_deleted(client: httpx.Client) -> None:
    recipe_id = _make_recipe(client, "Mistake From A Bad Import", [])

    assert client.delete(f"/api/recipes/{recipe_id}").status_code == 204
    assert client.get(f"/api/recipes/{recipe_id}").status_code == 404
    assert client.delete(f"/api/recipes/{recipe_id}").status_code == 404


def test_suggestions_pool_stock_across_every_location(client: httpx.Client) -> None:
    flour = _make_item(client, "Flour")
    yeast = _make_item(client, "Yeast")
    kitchen = _make_location(client, "Kitchen")
    garage = _make_location(client, "Garage Shelf")

    # Neither location alone can make this; together they can.
    _add_stock(client, item_id=flour, location_id=kitchen, quantity=5)
    _add_stock(client, item_id=yeast, location_id=garage, quantity=5)

    _make_recipe(
        client,
        "Bread",
        [
            {"item_id": flour, "quantity_required": 2, "unit": "each"},
            {"item_id": yeast, "quantity_required": 1, "unit": "each"},
        ],
    )

    per_location = client.get(f"/api/recipes/suggestions/{kitchen}").json()["suggestions"][0]
    assert per_location["available_score"] == pytest.approx(0.5)
    assert per_location["missing_items"] == ["Yeast"]

    pooled = client.get("/api/recipes/suggestions").json()
    assert pooled["location_id"] is None
    assert pooled["suggestions"][0]["available_score"] == pytest.approx(1.0)
    assert pooled["suggestions"][0]["missing_items"] == []


def test_cook_without_a_location_draws_from_every_location(client: httpx.Client) -> None:
    flour = _make_item(client, "Flour")
    kitchen = _make_location(client, "Kitchen")
    garage = _make_location(client, "Garage Shelf")

    # 3 in one place, 3 in another; the recipe needs 4, so neither alone suffices.
    _add_stock(client, item_id=flour, location_id=kitchen, quantity=3)
    _add_stock(client, item_id=flour, location_id=garage, quantity=3)

    recipe_id = _make_recipe(client, "Big Bake", [{"item_id": flour, "quantity_required": 4, "unit": "each"}])

    single = client.post(f"/api/recipes/{recipe_id}/cook", json={"location_id": kitchen, "servings": 1})
    assert single.status_code == 400
    assert "Insufficient" in single.json()["detail"]

    pooled = client.post(f"/api/recipes/{recipe_id}/cook", json={"servings": 1})
    assert pooled.status_code == 200, pooled.text
    # Spanned two locations, so two deductions.
    assert len(pooled.json()["deducted_transaction_ids"]) == 2

    def flour_at(location_id: str) -> float:
        rows = client.get(f"/api/inventory?location_id={location_id}").json()["items"]
        return float(next((row["quantity"] for row in rows if row["item_name"] == "Flour"), 0))

    # Neither lot is dated, so the tie-break is location name: "Garage Shelf"
    # drains fully before "Kitchen" is touched for the remaining 1.
    assert flour_at(garage) == pytest.approx(0.0)
    assert flour_at(kitchen) == pytest.approx(2.0)


def test_cook_shortfall_deducts_nothing_at_all(client: httpx.Client) -> None:
    """A cook that cannot complete must not consume the ingredients it could."""

    flour = _make_item(client, "Flour")
    sugar = _make_item(client, "Sugar")
    kitchen = _make_location(client, "Kitchen")

    _add_stock(client, item_id=flour, location_id=kitchen, quantity=10)
    # No sugar anywhere.

    recipe_id = _make_recipe(
        client,
        "Cake",
        [
            {"item_id": flour, "quantity_required": 2, "unit": "each"},
            {"item_id": sugar, "quantity_required": 1, "unit": "each"},
        ],
    )

    failed = client.post(f"/api/recipes/{recipe_id}/cook", json={"servings": 1})
    assert failed.status_code == 400
    assert "Sugar" in failed.json()["detail"]

    still_there = {
        row["item_name"]: row["quantity"]
        for row in client.get(f"/api/inventory?location_id={kitchen}").json()["items"]
    }
    assert float(still_there["Flour"]) == pytest.approx(10.0)


def test_parser_recovers_steps_that_lost_their_heading() -> None:
    """'Steps' is a heading, and numbered steps are never ingredients."""

    parsed = parse_recipe_text(
        "Pancakes\nIngredients:\n1 cup flour\n2 eggs\nSteps:\n1. Mix the batter well\n2. Fry until golden"
    )

    assert [row["name"] for row in parsed["ingredients"]] == ["flour", "eggs"]
    assert "Mix the batter well" in parsed["instructions"]
    assert "Fry until golden" in parsed["instructions"]


def test_parser_rescues_numbered_steps_with_no_instructions_heading() -> None:
    parsed = parse_recipe_text(
        "Pancakes\nIngredients:\n1 cup flour\n1. Mix the batter well until it is smooth\n2. Fry until golden"
    )

    assert [row["name"] for row in parsed["ingredients"]] == ["flour"]
    assert "Mix the batter well until it is smooth" in parsed["instructions"]
