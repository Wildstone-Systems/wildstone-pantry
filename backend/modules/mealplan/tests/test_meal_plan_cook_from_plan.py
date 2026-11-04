"""Cook from the plan (#32): link a real cook to a planned meal; never auto-link."""

from __future__ import annotations

import httpx
import pytest

from app.core.config import settings

TUESDAY = "2026-11-03"


@pytest.fixture(autouse=True)
def _personal_mode(monkeypatch) -> None:
    monkeypatch.setattr(settings, "auth_enabled", False)


@pytest.fixture
def kitchen(client: httpx.Client) -> dict:
    """A location stocked with plenty of beans, the one ingredient every test recipe uses."""
    loc = client.post("/api/locations", json={"name": "Kitchen"}).json()["id"]
    beans = client.post("/api/items", json={"name": "Beans"}).json()["id"]
    added = client.post("/api/inventory/add", json={"item_id": beans, "location_id": loc, "quantity": 50, "unit": "each"})
    assert added.status_code in (200, 201), added.text
    return {"location": loc, "beans": beans}


@pytest.fixture
def _recipe(client: httpx.Client, kitchen: dict):
    def make(_client: httpx.Client, name: str) -> str:
        recipe = client.post("/api/recipes", json={"name": name, "servings": 2}).json()["id"]
        put = client.put(
            f"/api/recipes/{recipe}/ingredients",
            json=[{"item_id": kitchen["beans"], "quantity_required": 1, "unit": "each"}],
        )
        assert put.status_code in (200, 201), put.text
        return recipe

    return make


@pytest.fixture
def _cook(client: httpx.Client, kitchen: dict):
    def cook(_client: httpx.Client, recipe_id: str) -> str:
        """Cook through the existing cook path; return its cook_event_id."""
        resp = client.post(f"/api/recipes/{recipe_id}/cook", json={"servings": 1, "location_id": kitchen["location"]})
        assert resp.status_code == 200, resp.text
        return resp.json()["cook_event_id"]

    return cook


def _plan(client: httpx.Client, **body) -> dict:
    body.setdefault("plan_date", TUESDAY)
    body.setdefault("meal_type", "dinner")
    resp = client.post("/api/meal-plan/entries", json=body)
    assert resp.status_code == 201, resp.text
    return resp.json()


def test_linking_a_cook_marks_the_meal_cooked(client: httpx.Client, _recipe, _cook) -> None:
    chilli = _recipe(client, "Chilli")
    entry = _plan(client, recipe_id=chilli)
    cook_id = _cook(client, chilli)

    resp = client.post(f"/api/meal-plan/entries/{entry['id']}/cooked", json={"cook_event_id": cook_id})
    assert resp.status_code == 200, resp.text
    assert (resp.json()["status"], resp.json()["cook_event_id"]) == ("cooked", cook_id)

    # Retrying the same link is harmless; a different cook is refused.
    again = client.post(f"/api/meal-plan/entries/{entry['id']}/cooked", json={"cook_event_id": cook_id})
    assert again.status_code == 200
    other = _cook(client, chilli)
    clash = client.post(f"/api/meal-plan/entries/{entry['id']}/cooked", json={"cook_event_id": other})
    assert clash.status_code == 409


def test_reset_unlinks_but_keeps_the_cook(client: httpx.Client, _recipe, _cook) -> None:
    chilli = _recipe(client, "Chilli")
    entry = _plan(client, recipe_id=chilli)
    cook_id = _cook(client, chilli)
    client.post(f"/api/meal-plan/entries/{entry['id']}/cooked", json={"cook_event_id": cook_id})

    reset = client.post(f"/api/meal-plan/entries/{entry['id']}/reset").json()
    assert (reset["status"], reset["cook_event_id"]) == ("planned", None)
    # A cooked meal can't be skipped; a reset one can.
    assert client.post(f"/api/meal-plan/entries/{entry['id']}/skip").status_code == 200


def test_cook_of_another_recipe_is_refused(client: httpx.Client, _recipe, _cook) -> None:
    entry = _plan(client, recipe_id=_recipe(client, "Chilli"))
    soup = _cook(client, _recipe(client, "Soup"))
    resp = client.post(f"/api/meal-plan/entries/{entry['id']}/cooked", json={"cook_event_id": soup})
    assert resp.status_code == 422


def test_only_recipe_meals_can_be_cooked(client: httpx.Client, _recipe, _cook) -> None:
    entry = _plan(client, title="Takeout")
    cook_id = _cook(client, _recipe(client, "Chilli"))
    resp = client.post(f"/api/meal-plan/entries/{entry['id']}/cooked", json={"cook_event_id": cook_id})
    assert resp.status_code == 409


def test_unknown_cook_is_refused(client: httpx.Client, _recipe, _cook) -> None:
    entry = _plan(client, recipe_id=_recipe(client, "Chilli"))
    missing = "00000000-0000-0000-0000-000000000000"
    resp = client.post(f"/api/meal-plan/entries/{entry['id']}/cooked", json={"cook_event_id": missing})
    assert resp.status_code == 422


def test_cooking_never_links_on_its_own(client: httpx.Client, _recipe, _cook) -> None:
    chilli = _recipe(client, "Chilli")
    entry = _plan(client, recipe_id=chilli)
    _cook(client, chilli)
    week = client.get("/api/meal-plan/entries", params={"start": "2026-11-01"}).json()["entries"]
    assert [(e["id"], e["status"]) for e in week] == [(entry["id"], "planned")]


def test_candidates_are_planned_meals_of_that_recipe_that_day(client: httpx.Client, _recipe, _cook) -> None:
    chilli, soup = _recipe(client, "Chilli"), _recipe(client, "Soup")
    dinner = _plan(client, recipe_id=chilli)
    _plan(client, recipe_id=chilli, plan_date="2026-11-04")  # another day
    _plan(client, recipe_id=soup)  # another recipe
    skipped = _plan(client, recipe_id=chilli, meal_type="lunch")
    client.post(f"/api/meal-plan/entries/{skipped['id']}/skip")

    resp = client.get("/api/meal-plan/cook-candidates", params={"recipe_id": chilli, "on": TUESDAY})
    assert resp.status_code == 200, resp.text
    assert [e["id"] for e in resp.json()] == [dinner["id"]]


def test_changing_what_a_cooked_meal_is_unlinks_the_cook(client: httpx.Client, _recipe, _cook) -> None:
    chilli, soup = _recipe(client, "Chilli"), _recipe(client, "Soup")
    entry = _plan(client, recipe_id=chilli)
    client.post(f"/api/meal-plan/entries/{entry['id']}/cooked", json={"cook_event_id": _cook(client, chilli)})
    url = f"/api/meal-plan/entries/{entry['id']}"

    # Saving it unchanged (the editor always sends the meal) keeps it cooked.
    kept = client.patch(url, json={"recipe_id": chilli, "note": "spicy"}).json()
    assert (kept["status"], kept["cook_event_id"] is not None) == ("cooked", True)

    # Making it a different meal: no longer the one that was cooked.
    changed = client.patch(url, json={"recipe_id": soup}).json()
    assert (changed["meal"], changed["status"], changed["cook_event_id"]) == ("Soup", "planned", None)
