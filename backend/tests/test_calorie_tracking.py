"""Per-person calorie tracking — step 4b.

Plating attributes food intake to a household profile: a ``consumed`` event with
``actor = profile`` and a calorie snapshot in the payload, decrementing the
balance. A daily rollup folds one profile's plates for a date against its target.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import httpx
from sqlalchemy import select

from app.db.session import SessionLocal
from modules.inventory.models import InventoryEvent


def _admin_headers(client: httpx.Client) -> dict[str, str]:
    client.post("/api/auth/bootstrap-admin", json={"email": "admin@pantryos.local", "password": "TestPass123!"})
    login = client.post("/api/auth/login", json={"email": "admin@pantryos.local", "password": "TestPass123!"})
    assert login.status_code == 200
    return {"Authorization": f"Bearer {login.json()['token']}"}


def _profile(client, h, *, name: str, target: int | None = None) -> str:
    body: dict = {"display_name": name}
    if target is not None:
        body["daily_calorie_target"] = target
    resp = client.post("/api/household/profiles", json=body, headers=h)
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def _item(client, h, *, name: str, calories_per_unit: float | None = None) -> str:
    body: dict = {"name": name}
    if calories_per_unit is not None:
        body["calories_per_unit"] = calories_per_unit
    resp = client.post("/api/items", json=body, headers=h)
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def _location(client, h, *, name: str) -> str:
    return client.post("/api/locations", json={"name": name}, headers=h).json()["id"]


def _stock(client, h, item_id, loc, qty) -> None:
    resp = client.post(
        "/api/inventory/add",
        json={"item_id": item_id, "location_id": loc, "quantity": qty, "unit": "each"},
        headers=h,
    )
    assert resp.status_code == 200, resp.text


def test_item_exposes_calories_per_unit_on_catalog_schemas(client: httpx.Client) -> None:
    h = _admin_headers(client)
    item = client.post("/api/items", json={"name": "Apple", "calories_per_unit": 95}, headers=h)
    assert item.status_code == 201
    assert Decimal(str(item.json()["calories_per_unit"])) == Decimal("95")

    fetched = client.get(f"/api/items/{item.json()['id']}", headers=h)
    assert Decimal(str(fetched.json()["calories_per_unit"])) == Decimal("95")


def test_plate_appends_attributed_consumed_event_with_calorie_snapshot(client: httpx.Client) -> None:
    h = _admin_headers(client)
    profile = _profile(client, h, name="Alex", target=2000)
    item_id = _item(client, h, name="Banana", calories_per_unit=105)
    loc = _location(client, h, name="Fruit Bowl")
    _stock(client, h, item_id, loc, 5)

    hp = {**h, "X-Household-Profile": profile}
    plate = client.post(
        "/api/consumption/plate",
        json={"item_id": item_id, "location_id": loc, "quantity": 2, "unit": "each"},
        headers=hp,
    )
    assert plate.status_code == 200, plate.text
    body = plate.json()
    assert body["profile_id"] == profile
    assert Decimal(str(body["calories"])) == Decimal("210")  # 2 * 105
    assert Decimal(str(body["balance"])) == Decimal("3")  # 5 - 2

    # Balance decremented in the projection-backed read.
    line = client.get(f"/api/inventory?location_id={loc}", headers=h).json()["items"][0]
    assert Decimal(str(line["quantity"])) == Decimal("3")

    # The ledger fact is a `consumed` event attributed to the eater with a snapshot.
    with SessionLocal() as db:
        events = db.execute(select(InventoryEvent).order_by(InventoryEvent.seq)).scalars().all()
        consumed = [e for e in events if e.event_type == "consumed"]
        assert len(consumed) == 1
        event = consumed[0]
        assert event.actor == profile
        assert event.payload is not None
        assert event.payload["calories"] == 210.0
        assert event.payload["profile_id"] == profile


def test_plate_without_a_profile_returns_400(client: httpx.Client) -> None:
    h = _admin_headers(client)
    item_id = _item(client, h, name="Cracker", calories_per_unit=10)
    loc = _location(client, h, name="Pantry")
    _stock(client, h, item_id, loc, 3)

    # No X-Household-Profile header and no body profile_id -> 400.
    resp = client.post(
        "/api/consumption/plate",
        json={"item_id": item_id, "location_id": loc, "quantity": 1, "unit": "each"},
        headers=h,
    )
    assert resp.status_code == 400


def test_plate_accepts_body_profile_id(client: httpx.Client) -> None:
    h = _admin_headers(client)
    profile = _profile(client, h, name="Sam", target=1800)
    item_id = _item(client, h, name="Yogurt", calories_per_unit=150)
    loc = _location(client, h, name="Fridge")
    _stock(client, h, item_id, loc, 4)

    resp = client.post(
        "/api/consumption/plate",
        json={"item_id": item_id, "location_id": loc, "quantity": 1, "profile_id": profile},
        headers=h,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["profile_id"] == profile


def test_item_with_null_calories_still_plates(client: httpx.Client) -> None:
    h = _admin_headers(client)
    profile = _profile(client, h, name="Jo")
    item_id = _item(client, h, name="Mystery Leftovers")  # no calories_per_unit
    loc = _location(client, h, name="Freezer")
    _stock(client, h, item_id, loc, 2)

    hp = {**h, "X-Household-Profile": profile}
    resp = client.post(
        "/api/consumption/plate",
        json={"item_id": item_id, "location_id": loc, "quantity": 1, "unit": "each"},
        headers=hp,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["calories"] is None

    # Balance still decremented.
    line = client.get(f"/api/inventory?location_id={loc}", headers=h).json()["items"][0]
    assert Decimal(str(line["quantity"])) == Decimal("1")


def test_daily_rollup_sums_only_that_profile_for_the_date(client: httpx.Client) -> None:
    h = _admin_headers(client)
    alex = _profile(client, h, name="Alex", target=2000)
    sam = _profile(client, h, name="Sam", target=1500)
    apple = _item(client, h, name="Apple", calories_per_unit=95)
    soup = _item(client, h, name="Soup", calories_per_unit=200)
    loc = _location(client, h, name="Kitchen")
    _stock(client, h, apple, loc, 10)
    _stock(client, h, soup, loc, 10)

    # Alex eats 2 apples (190) + 1 soup (200) = 390. Sam eats 1 apple (95).
    for item_id, qty, who in [(apple, 2, alex), (soup, 1, alex), (apple, 1, sam)]:
        r = client.post(
            "/api/consumption/plate",
            json={"item_id": item_id, "location_id": loc, "quantity": qty},
            headers={**h, "X-Household-Profile": who},
        )
        assert r.status_code == 200, r.text

    # Without a tz parameter these endpoints group events by UTC, independent
    # of the test runner's local calendar day.
    today = datetime.now(UTC).date().isoformat()
    rollup = client.get(f"/api/consumption/calories?profile_id={alex}&on={today}", headers=h)
    assert rollup.status_code == 200, rollup.text
    data = rollup.json()
    assert Decimal(str(data["total_calories"])) == Decimal("390")
    assert Decimal(str(data["target"])) == Decimal("2000")
    assert len(data["entries"]) == 2

    # A different date has no entries for the profile.
    empty = client.get(f"/api/consumption/calories?profile_id={alex}&on=2020-01-01", headers=h)
    assert Decimal(str(empty.json()["total_calories"])) == Decimal("0")
    assert empty.json()["entries"] == []


def test_household_summary_lists_every_profile(client: httpx.Client) -> None:
    h = _admin_headers(client)
    alex = _profile(client, h, name="Alex", target=2000)
    _profile(client, h, name="Sam", target=1500)
    apple = _item(client, h, name="Apple", calories_per_unit=95)
    loc = _location(client, h, name="Kitchen")
    _stock(client, h, apple, loc, 10)
    client.post(
        "/api/consumption/plate",
        json={"item_id": apple, "location_id": loc, "quantity": 2},
        headers={**h, "X-Household-Profile": alex},
    )

    # Without a tz parameter these endpoints group events by UTC, independent
    # of the test runner's local calendar day.
    today = datetime.now(UTC).date().isoformat()
    summary = client.get(f"/api/consumption/calories/summary?on={today}", headers=h).json()
    by_name = {p["display_name"]: Decimal(str(p["total_calories"])) for p in summary["profiles"]}
    assert by_name["Alex"] == Decimal("190")
    assert by_name["Sam"] == Decimal("0")
