"""Read-only iCal feed of the meal plan (#33)."""

from __future__ import annotations

from datetime import date, timedelta

import httpx

from modules.mealplan.calendar_feed import _escape, _fold


def _admin(client: httpx.Client) -> dict[str, str]:
    client.post("/api/auth/bootstrap-admin", json={"email": "admin@pantryos.local", "password": "TestPass123!"})
    login = client.post("/api/auth/login", json={"email": "admin@pantryos.local", "password": "TestPass123!"})
    return {"Authorization": f"Bearer {login.json()['token']}"}


def _plan(client: httpx.Client, day: date, **body) -> dict:
    body.setdefault("meal_type", "dinner")
    resp = client.post("/api/meal-plan/entries", json={"plan_date": day.isoformat(), **body})
    assert resp.status_code == 201, resp.text
    return resp.json()


def _link(client: httpx.Client, h: dict) -> str:
    resp = client.post("/api/meal-plan/calendar-feed", headers=h)
    assert resp.status_code == 201, resp.text
    return resp.json()["path"]


def test_feed_lists_planned_meals_as_all_day_events(client: httpx.Client) -> None:
    h = _admin(client)
    today = date.today()
    profile = client.post("/api/household/profiles", json={"display_name": "Sam"}, headers=h).json()["id"]
    soup = _plan(client, today, title="Soup, with bread", servings=3)
    _plan(client, today + timedelta(days=1), title="Shake", meal_type="snack", profile_id=profile)
    skipped = _plan(client, today + timedelta(days=2), title="Pizza")
    client.post(f"/api/meal-plan/entries/{skipped['id']}/skip")
    _plan(client, today + timedelta(days=90), title="Too far ahead")

    resp = client.get(_link(client, h))
    assert resp.status_code == 200, resp.text
    assert resp.headers["content-type"].startswith("text/calendar")
    body = resp.text
    assert body.startswith("BEGIN:VCALENDAR\r\n") and body.endswith("END:VCALENDAR\r\n")
    assert body.count("BEGIN:VEVENT") == 2  # skipped and out-of-window meals are left out
    assert f"UID:{soup['id']}@pantryos" in body
    assert f"DTSTART;VALUE=DATE:{today.strftime('%Y%m%d')}" in body
    assert "SUMMARY:Dinner: Soup\\, with bread" in body
    assert "DESCRIPTION:Serves 3" in body
    assert "SUMMARY:Snack: Shake (Sam)" in body


def test_feed_needs_the_current_secret(client: httpx.Client) -> None:
    h = _admin(client)
    assert client.get("/api/meal-plan/calendar.ics").status_code == 404
    assert client.get("/api/meal-plan/calendar.ics", params={"token": "mpcal_guess"}).status_code == 404

    old = _link(client, h)
    new = _link(client, h)  # a new link replaces the old one
    assert client.get(old).status_code == 404
    assert client.get(new).status_code == 200

    assert client.get("/api/meal-plan/calendar-feed", headers=h).json()["enabled"] is True
    assert client.delete("/api/meal-plan/calendar-feed", headers=h).status_code == 204
    assert client.get(new).status_code == 404
    assert client.get("/api/meal-plan/calendar-feed", headers=h).json()["enabled"] is False


def test_making_a_link_is_admin_only(client: httpx.Client) -> None:
    _admin(client)
    assert client.post("/api/meal-plan/calendar-feed").status_code in (401, 403)
    assert client.get("/api/meal-plan/calendar-feed").status_code in (401, 403)


def test_text_is_escaped_and_long_lines_folded() -> None:
    assert _escape("a,b;c\\d\ne") == "a\\,b\\;c\\\\d\\ne"
    line = "SUMMARY:" + "é" * 60
    folded = _fold(line)
    assert all(len(part.encode("utf-8")) <= 75 for part in folded.split("\r\n"))
    assert folded.replace("\r\n ", "") == line
