"""Meal plan shopping bridge (#31): gap, push, always-skip."""

from __future__ import annotations

import httpx
import pytest

from app.core.config import settings

SUNDAY = "2026-11-01"
TUESDAY = "2026-11-03"  # "today" in these tests


@pytest.fixture(autouse=True)
def _personal_mode(monkeypatch) -> None:
    monkeypatch.setattr(settings, "auth_enabled", False)


def _measured(client: httpx.Client, name: str) -> str:
    resp = client.post(
        "/api/items",
        json={"name": name, "stock_mode": "measured", "size_quantity": 1, "size_unit": "kg", "density_g_per_ml": 0.6},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def _stock(client: httpx.Client, item_id: str, quantity: float, unit: str) -> None:
    loc = client.post("/api/locations", json={"name": f"Shelf {item_id[:6]}"}).json()["id"]
    resp = client.post(
        "/api/inventory/add", json={"item_id": item_id, "location_id": loc, "quantity": quantity, "unit": unit}
    )
    assert resp.status_code in (200, 201), resp.text


@pytest.fixture
def cake(client: httpx.Client) -> dict:
    """Cake (serves 2): 1 cup flour, 1 cup sugar, optional salt. Plenty of flour, no sugar."""
    flour, sugar, salt = _measured(client, "Flour"), _measured(client, "Sugar"), _measured(client, "Salt")
    _stock(client, flour, 1000, "g")
    recipe = client.post("/api/recipes", json={"name": "Cake", "servings": 2}).json()["id"]
    resp = client.put(
        f"/api/recipes/{recipe}/ingredients",
        json=[
            {"item_id": flour, "quantity_required": 1, "unit": "cup"},
            {"item_id": sugar, "quantity_required": 1, "unit": "cup"},
            {"item_id": salt, "quantity_required": 1, "unit": "tsp", "optional": True},
        ],
    )
    assert resp.status_code in (200, 201), resp.text
    return {"recipe": recipe, "flour": flour, "sugar": sugar}


def _plan(client: httpx.Client, day: str, **body) -> dict:
    resp = client.post("/api/meal-plan/entries", json={"plan_date": day, "meal_type": "dinner", **body})
    assert resp.status_code == 201, resp.text
    return resp.json()


def _gap(client: httpx.Client, today: str = TUESDAY) -> dict:
    resp = client.get("/api/meal-plan/shopping-gap", params={"start": SUNDAY, "today": today})
    assert resp.status_code == 200, resp.text
    return resp.json()


def test_gap_sums_planned_meals_from_today_and_skips_what_is_stocked(client: httpx.Client, cake: dict) -> None:
    _plan(client, "2026-11-02", recipe_id=cake["recipe"], servings=2)  # before today: ignored
    _plan(client, TUESDAY, recipe_id=cake["recipe"], servings=2)
    _plan(client, "2026-11-05", recipe_id=cake["recipe"], servings=4)
    skipped = _plan(client, "2026-11-06", recipe_id=cake["recipe"], servings=2)
    client.post(f"/api/meal-plan/entries/{skipped['id']}/skip")
    _plan(client, "2026-11-06", title="Takeout")  # free text needs nothing

    gap = _gap(client)
    assert gap["start"] == TUESDAY
    lines = gap["lines"]
    assert [line["label"] for line in lines] == ["Sugar"]  # flour covered, salt optional
    sugar = lines[0]
    # 1 cup for 2 servings + 2 cups for 4 servings
    assert (sugar["unit"], float(sugar["needed"]), float(sugar["to_buy"]), sugar["status"]) == ("cup", 3, 3, "short")
    assert sugar["meals"] == ["Cake"]


def test_gap_counts_item_meals_in_each(client: httpx.Client) -> None:
    yoghurt = client.post("/api/items", json={"name": "Yoghurt"}).json()["id"]
    _stock(client, yoghurt, 1, "each")
    _plan(client, TUESDAY, item_id=yoghurt, servings=3)

    (line,) = _gap(client)["lines"]
    assert (line["label"], line["unit"], float(line["on_hand"]), float(line["to_buy"])) == ("Yoghurt", "each", 1, 2)


def test_week_in_the_past_has_no_gap(client: httpx.Client, cake: dict) -> None:
    _plan(client, TUESDAY, recipe_id=cake["recipe"])
    assert _gap(client, today="2026-11-09")["lines"] == []


def test_push_adds_chosen_lines_once(client: httpx.Client, cake: dict) -> None:
    _plan(client, TUESDAY, recipe_id=cake["recipe"], servings=2)
    key = _gap(client)["lines"][0]["key"]
    body = {"start": SUNDAY, "today": TUESDAY, "keys": [key]}

    first = client.post("/api/meal-plan/shopping-gap/push", json=body)
    assert first.status_code == 200, first.text
    assert first.json() == {"added": ["Sugar"], "already_on_list": []}

    again = client.post("/api/meal-plan/shopping-gap/push", json=body).json()
    assert again == {"added": [], "already_on_list": ["Sugar"]}

    items = client.get("/api/shopping-list").json()["items"]
    sugar = [i for i in items if i["name"] == "Sugar" and i["status"] == "pending"]
    assert len(sugar) == 1
    assert sugar[0]["source"] == "meal_plan"
    assert "1 cup" in sugar[0]["note"]


def test_push_ignores_unchosen_and_unknown_keys(client: httpx.Client, cake: dict) -> None:
    _plan(client, TUESDAY, recipe_id=cake["recipe"])
    resp = client.post(
        "/api/meal-plan/shopping-gap/push", json={"start": SUNDAY, "today": TUESDAY, "keys": ["canon:nope"]}
    )
    assert resp.json() == {"added": [], "already_on_list": []}


def test_always_skip_hides_a_staple_until_undone(client: httpx.Client, cake: dict) -> None:
    _plan(client, TUESDAY, recipe_id=cake["recipe"])
    line = _gap(client)["lines"][0]

    added = client.post("/api/meal-plan/excluded-items", json={"key": line["key"], "label": line["label"]})
    assert added.status_code == 201, added.text
    # Adding the same key twice is harmless.
    assert client.post("/api/meal-plan/excluded-items", json={"key": line["key"], "label": "x"}).status_code == 201

    gap = _gap(client)
    assert gap["lines"] == []
    assert [e["label"] for e in gap["excluded"]] == ["Sugar"]
    push = client.post(
        "/api/meal-plan/shopping-gap/push", json={"start": SUNDAY, "today": TUESDAY, "keys": [line["key"]]}
    )
    assert push.json()["added"] == []

    assert client.delete(f"/api/meal-plan/excluded-items/{added.json()['id']}").status_code == 204
    assert [l["label"] for l in _gap(client)["lines"]] == ["Sugar"]  # noqa: E741
    assert client.get("/api/meal-plan/excluded-items").json() == []


@pytest.mark.parametrize("key", ["", "canon:", "item:not-a-uuid", "flour"])
def test_always_skip_rejects_bad_keys(client: httpx.Client, key: str) -> None:
    assert client.post("/api/meal-plan/excluded-items", json={"key": key, "label": "X"}).status_code == 422


def test_unknown_exclusion_is_404(client: httpx.Client) -> None:
    missing = "00000000-0000-0000-0000-000000000000"
    assert client.delete(f"/api/meal-plan/excluded-items/{missing}").status_code == 404


def test_gap_needs_today(client: httpx.Client) -> None:
    assert client.get("/api/meal-plan/shopping-gap", params={"start": SUNDAY}).status_code == 422


# --- stock is shared once per ingredient (Codex review on #37) ------------------


def _flour_recipes(client: httpx.Client, flour: str) -> tuple[str, str]:
    grams = client.post("/api/recipes", json={"name": "Bread", "servings": 1}).json()["id"]
    client.put(f"/api/recipes/{grams}/ingredients", json=[{"item_id": flour, "quantity_required": 100, "unit": "g"}])
    kilos = client.post("/api/recipes", json={"name": "Pizza", "servings": 1}).json()["id"]
    client.put(f"/api/recipes/{kilos}/ingredients", json=[{"item_id": flour, "quantity_required": 0.1, "unit": "kg"}])
    return grams, kilos


def test_stock_is_not_spent_twice_across_units(client: httpx.Client) -> None:
    flour = _measured(client, "Flour")
    _stock(client, flour, 100, "g")
    grams, kilos = _flour_recipes(client, flour)
    _plan(client, TUESDAY, recipe_id=grams, servings=1)
    _plan(client, "2026-11-04", recipe_id=kilos, servings=1)

    lines = _gap(client)["lines"]
    # 200 g needed in total, 100 g on hand: one of the two lines must still buy 100 g.
    assert [(line["unit"], float(line["to_buy"])) for line in lines] == [("kg", 0.1)]
    assert float(lines[0]["on_hand"]) == 0


def test_item_meals_pool_stock_across_brands(client: httpx.Client) -> None:
    a = client.post("/api/items", json={"name": "Yoghurt", "brand": "A"}).json()["id"]
    b = client.post("/api/items", json={"name": "Yoghurt", "brand": "B"}).json()["id"]
    _stock(client, a, 1, "each")
    _stock(client, b, 1, "each")
    _plan(client, TUESDAY, item_id=a, servings=1)
    _plan(client, "2026-11-04", item_id=b, servings=1)

    assert _gap(client)["lines"] == []  # 2 needed, 2 on hand across both brands


def test_push_combines_units_of_one_ingredient_into_one_entry(client: httpx.Client) -> None:
    flour = _measured(client, "Flour")
    grams, kilos = _flour_recipes(client, flour)
    _plan(client, TUESDAY, recipe_id=grams, servings=1)
    _plan(client, "2026-11-04", recipe_id=kilos, servings=1)
    key = _gap(client)["lines"][0]["key"]

    result = client.post(
        "/api/meal-plan/shopping-gap/push", json={"start": SUNDAY, "today": TUESDAY, "keys": [key]}
    ).json()
    assert result == {"added": ["Flour"], "already_on_list": []}
    (entry,) = [i for i in client.get("/api/shopping-list").json()["items"] if i["name"] == "Flour"]
    assert "100 g" in entry["note"] and "0.1 kg" in entry["note"]
    assert "Bread" in entry["note"] and "Pizza" in entry["note"]
