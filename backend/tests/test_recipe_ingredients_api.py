"""POST/PUT /api/recipes accept ingredients (by name or id); unknown fields 422.

Closes the gap an agent hit: `POST /api/recipes` silently dropped an
`ingredients` array, and there was no obvious one-call way to create a recipe
with named ingredients.
"""

from __future__ import annotations

import httpx


def test_create_recipe_with_named_ingredients_creates_items_and_links(client: httpx.Client) -> None:
    resp = client.post(
        "/api/recipes",
        json={
            "name": "Spaghetti Bolognese",
            "servings": 6,
            "ingredients": [
                {"name": "ground beef", "quantity": 1, "unit": "lb"},
                {"name": "spaghetti", "quantity": 1, "unit": "lb"},
            ],
        },
    )
    assert resp.status_code == 200, resp.text
    recipe_id = resp.json()["id"]

    got = client.get(f"/api/recipes/{recipe_id}").json()
    names = {i["item_name"] for i in got["ingredients"]}
    assert names == {"ground beef", "spaghetti"}
    # The named items now exist in the catalog.
    catalog = {i["name"] for i in client.get("/api/items").json()["items"]}
    assert {"ground beef", "spaghetti"} <= catalog


def test_create_recipe_with_ingredient_by_item_id(client: httpx.Client) -> None:
    item = client.post("/api/items", json={"name": "Butter"}).json()
    resp = client.post(
        "/api/recipes",
        json={"name": "Buttered Toast", "ingredients": [{"item_id": item["id"], "quantity": 2, "unit": "tbsp"}]},
    )
    assert resp.status_code == 200, resp.text
    got = client.get(f"/api/recipes/{resp.json()['id']}").json()
    assert got["ingredients"][0]["item_id"] == item["id"]


def test_unknown_field_is_rejected_with_422_not_silently_dropped(client: httpx.Client) -> None:
    resp = client.post("/api/recipes", json={"name": "Oops", "totally_made_up": 1})
    assert resp.status_code == 422


def test_metadata_update_leaves_ingredients_untouched(client: httpx.Client) -> None:
    created = client.post(
        "/api/recipes",
        json={"name": "Stew", "ingredients": [{"name": "carrot", "quantity": 3, "unit": "each"}]},
    ).json()
    recipe_id = created["id"]

    # PUT without `ingredients` changes only metadata.
    upd = client.put(f"/api/recipes/{recipe_id}", json={"name": "Beef Stew"})
    assert upd.status_code == 200, upd.text
    assert upd.json()["name"] == "Beef Stew"

    got = client.get(f"/api/recipes/{recipe_id}").json()
    assert [i["item_name"] for i in got["ingredients"]] == ["carrot"]  # preserved


def test_empty_ingredients_array_clears_them(client: httpx.Client) -> None:
    created = client.post(
        "/api/recipes",
        json={"name": "Cleared", "ingredients": [{"name": "salt", "quantity": 1, "unit": "tsp"}]},
    ).json()
    recipe_id = created["id"]

    client.put(f"/api/recipes/{recipe_id}", json={"name": "Cleared", "ingredients": []})
    got = client.get(f"/api/recipes/{recipe_id}").json()
    assert got["ingredients"] == []


def test_unknown_ingredient_without_create_missing_is_rejected(client: httpx.Client) -> None:
    resp = client.post(
        "/api/recipes",
        json={
            "name": "Strict",
            "create_missing_items": False,
            "ingredients": [{"name": "does-not-exist-item", "quantity": 1, "unit": "each"}],
        },
    )
    assert resp.status_code == 400
    assert "does-not-exist-item" in resp.json()["detail"]
