""""Today" on the Calories page must mean the household's local day, not the
server's UTC day — the bug report: a meal logged in the evening (US Eastern)
vanished off the page a few minutes later, purely because the server's clock
crossed UTC midnight (hours before the household's actual midnight)."""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from uuid import UUID

import httpx
from sqlalchemy import select

from app.db.session import SessionLocal
from modules.inventory.models import InventoryEvent


def _admin_headers(client: httpx.Client) -> dict[str, str]:
    client.post("/api/auth/bootstrap-admin", json={"email": "admin@pantryos.local", "password": "TestPass123!"})
    login = client.post("/api/auth/login", json={"email": "admin@pantryos.local", "password": "TestPass123!"})
    assert login.status_code == 200
    return {"Authorization": f"Bearer {login.json()['token']}"}


def _backdate(event_id: str, occurred_at: datetime) -> None:
    """Move an event's timestamp, via the ORM.

    Raw SQL with the dashed UUID string silently matched ZERO rows (the column
    stores it undashed), so these tests were passing only because "now" happened
    to fall on the expected local day at the time they ran. The read-back below
    makes a no-op update fail loudly instead of quietly.
    """
    with SessionLocal() as db:
        event = db.execute(
            select(InventoryEvent).where(InventoryEvent.event_id == UUID(event_id))
        ).scalar_one()
        event.occurred_at = occurred_at
        db.add(event)
        db.commit()
        stored = db.execute(
            select(InventoryEvent.occurred_at).where(InventoryEvent.event_id == UUID(event_id))
        ).scalar_one()
        assert stored.replace(tzinfo=None) == occurred_at.replace(tzinfo=None), "backdate did not apply"


def test_evening_meal_stays_on_the_local_day_across_utc_midnight(client: httpx.Client) -> None:
    h = _admin_headers(client)
    profile = client.post("/api/household/profiles", json={"display_name": "Alex"}, headers=h).json()["id"]
    item_id = client.post("/api/items", json={"name": "Crackers", "calories_per_unit": 120}, headers=h).json()["id"]

    hp = {**h, "X-Household-Profile": profile}
    plate = client.post("/api/consumption/plate", json={"item_id": item_id, "quantity": 1, "unit": "each"}, headers=hp)
    event_id = plate.json()["event_id"]

    # 10:30 PM Eastern on Aug 2 — but already past midnight UTC (Aug 3). The
    # exact real-world timing of the bug report.
    _backdate(event_id, datetime(2026, 8, 3, 2, 30, tzinfo=timezone.utc))

    # Correct: the household's local day (America/New_York) is still Aug 2.
    local = client.get(
        "/api/consumption/calories",
        params={"profile_id": profile, "on": "2026-08-02", "tz": "America/New_York"},
        headers=h,
    ).json()
    assert Decimal(str(local["total_calories"])) == Decimal("120")
    assert len(local["entries"]) == 1

    # It must NOT also show up under Aug 3 local (double-counting/wrong-day).
    tomorrow_local = client.get(
        "/api/consumption/calories",
        params={"profile_id": profile, "on": "2026-08-03", "tz": "America/New_York"},
        headers=h,
    ).json()
    assert Decimal(str(tomorrow_local["total_calories"])) == Decimal("0")
    assert tomorrow_local["entries"] == []

    # No tz given (a tz-less caller) falls back to plain UTC bucketing, where
    # this timestamp genuinely is Aug 3.
    utc_default = client.get(
        "/api/consumption/calories", params={"profile_id": profile, "on": "2026-08-03"}, headers=h
    ).json()
    assert Decimal(str(utc_default["total_calories"])) == Decimal("120")


def test_unknown_tz_falls_back_to_utc_instead_of_500ing(client: httpx.Client) -> None:
    h = _admin_headers(client)
    profile = client.post("/api/household/profiles", json={"display_name": "Sam"}, headers=h).json()["id"]
    resp = client.get(
        "/api/consumption/calories",
        params={"profile_id": profile, "on": date.today().isoformat(), "tz": "Not/ARealZone"},
        headers=h,
    )
    assert resp.status_code == 200, resp.text


def test_household_summary_honors_tz_too(client: httpx.Client) -> None:
    h = _admin_headers(client)
    profile = client.post("/api/household/profiles", json={"display_name": "Robin"}, headers=h).json()["id"]
    item_id = client.post("/api/items", json={"name": "Chips", "calories_per_unit": 200}, headers=h).json()["id"]
    hp = {**h, "X-Household-Profile": profile}
    plate = client.post("/api/consumption/plate", json={"item_id": item_id, "quantity": 1, "unit": "each"}, headers=hp)
    _backdate(plate.json()["event_id"], datetime(2026, 8, 3, 2, 30, tzinfo=timezone.utc))

    summary = client.get(
        "/api/consumption/calories/summary", params={"on": "2026-08-02", "tz": "America/New_York"}, headers=h
    ).json()
    by_name = {p["display_name"]: Decimal(str(p["total_calories"])) for p in summary["profiles"]}
    assert by_name["Robin"] == Decimal("200")


def test_todays_calories_default_to_the_households_day_not_the_servers(client: httpx.Client) -> None:
    """Same bug as above, but on the DEFAULT day rather than an explicit one.

    The route defaulted `on` from the server's clock, which resolves before `tz`
    is even read. Between a household's evening and UTC midnight the two dates
    disagree, so "today's calories" asked for tomorrow's log and came back
    empty — a meal appeared to vanish the moment it was saved.

    Both zones are asserted so this can't pass by luck of which side of the UTC
    line the suite happens to run on.
    """
    h = _admin_headers(client)
    profile = client.post("/api/household/profiles", json={"display_name": "Alex"}, headers=h).json()["id"]
    item_id = client.post("/api/items", json={"name": "Crackers", "calories_per_unit": 120}, headers=h).json()["id"]

    hp = {**h, "X-Household-Profile": profile}
    plate = client.post("/api/consumption/plate", json={"item_id": item_id, "quantity": 1, "unit": "each"}, headers=hp)
    assert plate.status_code in (200, 201), plate.text

    for zone in ("America/New_York", "Pacific/Auckland"):
        rollup = client.get(
            "/api/consumption/calories",
            params={"profile_id": profile, "tz": zone},
            headers=h,
        )
        assert rollup.status_code == 200, rollup.text
        assert float(rollup.json()["total_calories"]) == 120, f"{zone}: meal missing from today's log"
