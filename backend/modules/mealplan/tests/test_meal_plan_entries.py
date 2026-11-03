"""Meal plan week calendar (#29): add, list, move, edit, skip/reset, delete."""

from __future__ import annotations

from datetime import date

import httpx
import pytest

from modules.mealplan.services import week_start

SUNDAY = "2026-11-01"


def _recipe(client: httpx.Client, name: str = "Baked Turkey", servings: int = 6) -> dict:
    resp = client.post("/api/recipes", json={"name": name, "servings": servings})
    assert resp.status_code in (200, 201), resp.text
    return resp.json()


def _add(client: httpx.Client, **body) -> httpx.Response:
    body.setdefault("plan_date", SUNDAY)
    body.setdefault("meal_type", "dinner")
    return client.post("/api/meal-plan/entries", json=body)


def _week(client: httpx.Client, start: str = SUNDAY) -> list[dict]:
    resp = client.get("/api/meal-plan/entries", params={"start": start})
    assert resp.status_code == 200, resp.text
    return resp.json()["entries"]


def test_week_starts_on_sunday() -> None:
    assert week_start(date(2026, 11, 1)) == date(2026, 11, 1)  # Sunday
    assert week_start(date(2026, 11, 4)) == date(2026, 11, 1)  # Wednesday
    assert week_start(date(2026, 11, 7)) == date(2026, 11, 1)  # Saturday


def test_recipe_entry_defaults_servings_from_recipe(client: httpx.Client) -> None:
    recipe = _recipe(client)
    resp = _add(client, recipe_id=recipe["id"])
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["kind"] == "recipe"
    assert body["meal"] == "Baked Turkey"
    assert body["servings"] == 6
    assert body["status"] == "planned"
    assert body["source"] == "manual"
    assert body["profile_id"] is None


def test_servings_are_pinned_on_the_entry(client: httpx.Client) -> None:
    recipe = _recipe(client)
    body = _add(client, recipe_id=recipe["id"], servings=2).json()
    assert body["servings"] == 2


def test_main_and_side_share_a_cell_in_order(client: httpx.Client) -> None:
    main = _add(client, recipe_id=_recipe(client)["id"]).json()
    side = _add(client, recipe_id=_recipe(client, "Green Bean Casserole", 4)["id"]).json()
    assert (main["position"], side["position"]) == (0, 1)
    assert [e["meal"] for e in _week(client)] == ["Baked Turkey", "Green Bean Casserole"]


def test_free_text_and_item_entries(client: httpx.Client) -> None:
    text = _add(client, title="  Takeout  ").json()
    assert (text["kind"], text["meal"], text["servings"]) == ("text", "Takeout", 1)

    item = client.post("/api/items", json={"name": "Leftover chilli"}).json()
    body = _add(client, item_id=item["id"], meal_type="lunch").json()
    assert (body["kind"], body["meal"]) == ("item", "Leftover chilli")


@pytest.mark.parametrize(
    "body",
    [
        {},  # names nothing
        {"title": "   "},  # blank text
        {"title": "Takeout", "recipe_id": "00000000-0000-0000-0000-000000000001"},  # two at once
        {"recipe_id": "00000000-0000-0000-0000-000000000001"},  # unknown recipe
        {"title": "Soup", "profile_id": "00000000-0000-0000-0000-000000000001"},  # unknown person
    ],
)
def test_invalid_entries_are_rejected(client: httpx.Client, body: dict) -> None:
    assert _add(client, **body).status_code == 422


def test_unknown_meal_type_is_rejected(client: httpx.Client) -> None:
    assert _add(client, title="Brunch", meal_type="brunch").status_code == 422


def test_entry_for_one_person(client: httpx.Client) -> None:
    profile = client.post("/api/household/profiles", json={"display_name": "Sam"}).json()
    body = _add(client, title="Protein shake", meal_type="snack", profile_id=profile["id"]).json()
    assert body["profile_id"] == profile["id"]


def test_recipe_entry_follows_renames(client: httpx.Client) -> None:
    recipe = _recipe(client)
    _add(client, recipe_id=recipe["id"])
    renamed = client.put(f"/api/recipes/{recipe['id']}", json={"name": "Herb Roast Turkey"})
    assert renamed.status_code == 200, renamed.text
    assert _week(client)[0]["meal"] == "Herb Roast Turkey"


def test_deleting_the_recipe_keeps_the_entry(client: httpx.Client) -> None:
    recipe = _recipe(client)
    _add(client, recipe_id=recipe["id"])
    assert client.delete(f"/api/recipes/{recipe['id']}").status_code == 204
    entries = _week(client)
    assert len(entries) == 1
    assert entries[0]["meal"] == "Baked Turkey"


def test_week_window_excludes_other_weeks(client: httpx.Client) -> None:
    _add(client, title="This week", plan_date="2026-11-07")
    _add(client, title="Next week", plan_date="2026-11-08")
    _add(client, title="Last week", plan_date="2026-10-31")
    assert [e["meal"] for e in _week(client)] == ["This week"]


def test_move_to_another_day_goes_to_end_of_cell(client: httpx.Client) -> None:
    _add(client, title="Already there", plan_date="2026-11-02")
    moving = _add(client, title="Chilli").json()
    resp = client.patch(f"/api/meal-plan/entries/{moving['id']}", json={"plan_date": "2026-11-02"})
    assert resp.status_code == 200, resp.text
    assert (resp.json()["plan_date"], resp.json()["position"]) == ("2026-11-02", 1)


def test_edit_servings_note_and_meal(client: httpx.Client) -> None:
    entry = _add(client, title="Takeout").json()
    recipe = _recipe(client)
    resp = client.patch(
        f"/api/meal-plan/entries/{entry['id']}",
        json={"servings": 3, "note": "use the big pan", "recipe_id": recipe["id"]},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert (body["kind"], body["meal"], body["servings"], body["note"]) == (
        "recipe",
        "Baked Turkey",
        3,
        "use the big pan",
    )


def test_edit_cannot_clear_required_fields(client: httpx.Client) -> None:
    entry = _add(client, title="Takeout").json()
    assert client.patch(f"/api/meal-plan/entries/{entry['id']}", json={"servings": None}).status_code == 422
    assert client.patch(f"/api/meal-plan/entries/{entry['id']}", json={"title": None}).status_code == 422


def test_skip_and_reset(client: httpx.Client) -> None:
    entry = _add(client, title="Chilli").json()
    url = f"/api/meal-plan/entries/{entry['id']}"
    assert client.post(f"{url}/skip").json()["status"] == "skipped"
    assert client.post(f"{url}/skip").status_code == 409
    assert client.post(f"{url}/reset").json()["status"] == "planned"


def test_delete(client: httpx.Client) -> None:
    entry = _add(client, title="Chilli").json()
    assert client.delete(f"/api/meal-plan/entries/{entry['id']}").status_code == 204
    assert _week(client) == []
    assert client.delete(f"/api/meal-plan/entries/{entry['id']}").status_code == 404


def test_week_needs_a_start_and_sane_days(client: httpx.Client) -> None:
    assert client.get("/api/meal-plan/entries").status_code == 422
    assert client.get("/api/meal-plan/entries", params={"start": SUNDAY, "days": 0}).status_code == 422
    assert client.get("/api/meal-plan/entries", params={"start": SUNDAY, "days": 43}).status_code == 422
