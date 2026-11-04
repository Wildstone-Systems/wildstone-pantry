"""Wildstone ↔ meal plan (#33): query, agent planning, and the change event."""

from __future__ import annotations

from datetime import timedelta

import httpx

from app.db.session import SessionLocal
from modules.wildstone.models import WildstoneOutbox
from modules.wildstone.services.relay import run_pass

BASE = "/api/integrations/wildstone"
V1 = f"{BASE}/v1"
SUNDAY = "2026-11-01"


def _admin(client: httpx.Client) -> dict[str, str]:
    client.post("/api/auth/bootstrap-admin", json={"email": "admin@pantryos.local", "password": "TestPass123!"})
    login = client.post("/api/auth/login", json={"email": "admin@pantryos.local", "password": "TestPass123!"})
    return {"Authorization": f"Bearer {login.json()['token']}"}


def _agent(client: httpx.Client, h: dict, scopes: str = "read,write") -> dict[str, str]:
    created = client.post("/api/agent-keys", json={"name": "Wildstone", "scopes": scopes}, headers=h)
    assert created.status_code == 200, created.text
    return {"X-Agent-Key": created.json()["secret"]}


def _setup(client: httpx.Client):
    h = _admin(client)
    agent = _agent(client, h)
    chilli = client.post("/api/recipes", json={"name": "Chilli", "servings": 4}, headers=h).json()["id"]
    sam = client.post("/api/household/profiles", json={"display_name": "Sam"}, headers=h).json()["id"]
    client.put(f"{BASE}/members/{sam}", json={"member": "sam"}, headers=h).raise_for_status()
    return h, agent, chilli, sam


def _act(client, agent, name: str, body: dict) -> httpx.Response:
    return client.post(f"{V1}/act/{name}", json=body, headers=agent)


def _query(client, agent, params: dict) -> httpx.Response:
    return client.get(f"{V1}/query/pantry.meal_plan", params=params, headers=agent)


def test_capabilities_advertise_the_meal_plan(client: httpx.Client) -> None:
    h, agent, *_ = _setup(client)
    caps = {(c["kind"], c["name"]): c for c in client.get(f"{V1}/capabilities", headers=agent).json()["capabilities"]}
    assert ("query", "pantry.meal_plan") in caps
    assert ("event", "pantry.meal_plan_changed") in caps
    for name in ("pantry.meal_plan.add", "pantry.meal_plan.week", "pantry.meal_plan.remove"):
        assert caps[("action", name)]["proposedRisk"] == "domestic"


def test_an_agent_plans_a_meal_by_recipe_name_for_a_member(client: httpx.Client) -> None:
    h, agent, chilli, sam = _setup(client)
    resp = _act(client, agent, "pantry.meal_plan.add",
                {"date": "2026-11-05", "meal_type": "dinner", "recipe": "chilli", "member": "sam", "servings": 2})
    assert resp.status_code == 200, resp.text
    entry = resp.json()["data"]["entry"]
    assert (entry["meal"], entry["recipe_id"], entry["servings"], entry["for"], entry["source"]) == (
        "Chilli", chilli, 2, "sam", "agent",
    )

    # It is an ordinary entry: the app shows it, and anyone can change it.
    week = client.get("/api/meal-plan/entries", params={"start": SUNDAY}, headers=h).json()["entries"]
    assert [(e["meal"], e["profile_id"], e["source"]) for e in week] == [("Chilli", sam, "agent")]
    assert client.patch(f"/api/meal-plan/entries/{week[0]['id']}", json={"servings": 4}, headers=h).status_code == 200


def test_query_a_week_and_a_single_day(client: httpx.Client) -> None:
    h, agent, *_ = _setup(client)
    _act(client, agent, "pantry.meal_plan.add", {"date": "2026-11-03", "title": "Takeout"})
    _act(client, agent, "pantry.meal_plan.add", {"date": "2026-11-05", "recipe": "Chilli"})

    week = _query(client, agent, {"start": SUNDAY}).json()
    assert week["status"] == "ok"
    assert [(e["date"], e["meal"], e["for"]) for e in week["data"]["entries"]] == [
        ("2026-11-03", "Takeout", None),
        ("2026-11-05", "Chilli", None),
    ]
    day = _query(client, agent, {"date": "2026-11-05"}).json()["data"]
    assert (day["days"], [e["meal"] for e in day["entries"]]) == (1, ["Chilli"])

    # Nothing planned is "empty", not an error.
    assert _query(client, agent, {"date": "2026-12-25"}).json()["status"] == "empty"


def test_an_uncertain_recipe_asks_instead_of_guessing(client: httpx.Client) -> None:
    h, agent, *_ = _setup(client)
    client.post("/api/recipes", json={"name": "Chicken Soup"}, headers=h)
    client.post("/api/recipes", json={"name": "Chicken Curry"}, headers=h)
    resp = _act(client, agent, "pantry.meal_plan.add", {"date": "2026-11-05", "recipe": "chicken"})
    assert resp.status_code == 422
    detail = resp.json()["detail"]
    assert "Chicken Soup" in detail and "Chicken Curry" in detail and "recipe_id" in detail

    missing = _act(client, agent, "pantry.meal_plan.add", {"date": "2026-11-05", "recipe": "lasagne"})
    assert missing.status_code == 422 and "title" in missing.json()["detail"]


def test_a_week_is_planned_all_or_nothing(client: httpx.Client) -> None:
    h, agent, *_ = _setup(client)
    bad = _act(client, agent, "pantry.meal_plan.week", {"meals": [
        {"date": "2026-11-02", "recipe": "Chilli"},
        {"date": "2026-11-03", "recipe": "nothing like this"},
    ]})
    assert bad.status_code == 422 and "meal 2:" in bad.json()["detail"]
    assert _query(client, agent, {"start": SUNDAY}).json()["status"] == "empty"

    good = _act(client, agent, "pantry.meal_plan.week", {"meals": [
        {"date": "2026-11-02", "recipe": "Chilli"},
        {"date": "2026-11-02", "title": "Garlic bread"},
        {"date": "2026-11-03", "meal_type": "lunch", "title": "Leftovers", "member": "sam"},
    ]})
    assert good.status_code == 200, good.text
    assert good.json()["data"]["added"] == 3
    entries = _query(client, agent, {"start": SUNDAY}).json()["data"]["entries"]
    assert [(e["date"], e["meal"]) for e in entries] == [
        ("2026-11-02", "Chilli"), ("2026-11-02", "Garlic bread"), ("2026-11-03", "Leftovers"),
    ]


def test_remove_is_repeat_safe(client: httpx.Client) -> None:
    h, agent, *_ = _setup(client)
    entry = _act(client, agent, "pantry.meal_plan.add", {"date": "2026-11-05", "title": "Pizza"}).json()["data"]["entry"]
    first = _act(client, agent, "pantry.meal_plan.remove", {"entry_id": entry["entry_id"]}).json()["data"]
    again = _act(client, agent, "pantry.meal_plan.remove", {"entry_id": entry["entry_id"]}).json()["data"]
    assert (first["already_removed"], again["already_removed"]) == (False, True)


def test_planning_needs_the_write_scope(client: httpx.Client) -> None:
    h = _admin(client)
    reader = _agent(client, h, scopes="read")
    assert _query(client, reader, {"start": SUNDAY}).status_code == 200
    assert _act(client, reader, "pantry.meal_plan.add", {"date": "2026-11-05", "title": "Pizza"}).status_code == 403


def test_bad_requests_are_loud(client: httpx.Client) -> None:
    h, agent, *_ = _setup(client)
    for body in ({}, {"date": "tomorrow", "title": "x"}, {"date": "2026-11-05"},
                 {"date": "2026-11-05", "title": "x", "meal_type": "brunch"},
                 {"date": "2026-11-05", "title": "x", "member": "nobody"},
                 {"date": "2026-11-05", "title": "x", "servings": 0}):
        assert _act(client, agent, "pantry.meal_plan.add", body).status_code == 422, body
    assert _act(client, agent, "pantry.meal_plan.week", {"meals": []}).status_code == 422
    assert _query(client, agent, {"days": 99}).status_code == 422


# --- pantry.meal_plan_changed ---------------------------------------------------


def _connect(client: httpx.Client, h: dict) -> None:
    resp = client.put(f"{BASE}/connection", json={"keystone_url": "https://wildstone.test/", "publisher_token": "pub"}, headers=h)
    assert resp.status_code == 200, resp.text


def _pass(quiet: timedelta = timedelta(0)) -> int:
    with SessionLocal() as db:
        return run_pass(db, settle=timedelta(0), receipt_quiet=timedelta(0), list_quiet=quiet)


def _changes() -> list[dict]:
    with SessionLocal() as db:
        return [
            row.envelope
            for row in db.query(WildstoneOutbox).order_by(WildstoneOutbox.id).all()
            if row.event_type == "pantry.meal_plan_changed"
        ]


def test_a_burst_of_plan_edits_is_one_change(client: httpx.Client) -> None:
    h, agent, *_ = _setup(client)
    client.post("/api/meal-plan/entries", json={"plan_date": "2099-01-01", "meal_type": "dinner", "title": "Old"}, headers=h)
    _connect(client, h)
    assert _pass() == 0  # the plan as it stood when connecting is not news

    _act(client, agent, "pantry.meal_plan.week", {"meals": [
        {"date": "2099-01-02", "title": "Soup"}, {"date": "2099-01-03", "title": "Stew"},
    ]})
    assert _pass(quiet=timedelta(hours=1)) == 0  # still settling: nothing yet
    _pass()
    assert [c["payload"] for c in _changes()] == [{"upcoming_meals": 3}]
    assert _pass() == 0  # read again: nothing new

    # Removing one is a new change; removing the rest (an empty plan) too.
    week = client.get("/api/meal-plan/entries", params={"start": "2098-12-28", "days": 14}, headers=h).json()["entries"]
    client.delete(f"/api/meal-plan/entries/{week[0]['id']}", headers=h)
    _pass()
    for e in week[1:]:
        client.delete(f"/api/meal-plan/entries/{e['id']}", headers=h)
    _pass()
    changes = _changes()
    assert [c["payload"]["upcoming_meals"] for c in changes] == [3, 2, 0]
    assert len({c["eventId"] for c in changes}) == 3
