"""Deleting a mistaken plate ("I didn't actually have coconut oil") — the
Calories page's delete button. Never a hard delete (ADR 0002): restores any
inventory the plate decremented and nets the calorie total to zero, while
hiding the mistake (and its correction) from the displayed entries list.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import httpx


def _admin_headers(client: httpx.Client) -> dict[str, str]:
    client.post("/api/auth/bootstrap-admin", json={"email": "admin@pantryos.local", "password": "TestPass123!"})
    login = client.post("/api/auth/login", json={"email": "admin@pantryos.local", "password": "TestPass123!"})
    assert login.status_code == 200
    return {"Authorization": f"Bearer {login.json()['token']}"}


def _profile(client, h, *, name: str) -> str:
    return client.post("/api/household/profiles", json={"display_name": name}, headers=h).json()["id"]


def _item(client, h, *, name: str, calories_per_unit: float) -> str:
    return client.post("/api/items", json={"name": name, "calories_per_unit": calories_per_unit}, headers=h).json()["id"]


def _location(client, h, *, name: str) -> str:
    return client.post("/api/locations", json={"name": name}, headers=h).json()["id"]


def test_deleting_a_pantry_plate_restores_stock_and_zeroes_the_total(client: httpx.Client) -> None:
    h = _admin_headers(client)
    profile = _profile(client, h, name="Alex")
    item_id = _item(client, h, name="Organic Virgin Coconut Oil", calories_per_unit=120)
    loc = _location(client, h, name="Pantry")
    client.post("/api/inventory/add", json={"item_id": item_id, "location_id": loc, "quantity": 5, "unit": "each"}, headers=h)

    hp = {**h, "X-Household-Profile": profile}
    plate = client.post(
        "/api/consumption/plate", json={"item_id": item_id, "location_id": loc, "quantity": 1, "unit": "each"}, headers=hp
    )
    assert plate.status_code == 200, plate.text
    event_id = plate.json()["event_id"]

    line = client.get(f"/api/inventory?location_id={loc}", headers=h).json()["items"][0]
    assert Decimal(str(line["quantity"])) == Decimal("4")  # 5 - 1

    today = datetime.now(UTC).date().isoformat()
    before = client.get(f"/api/consumption/calories?profile_id={profile}&on={today}", headers=h).json()
    assert Decimal(str(before["total_calories"])) == Decimal("120")
    assert len(before["entries"]) == 1
    assert before["entries"][0]["event_id"] == event_id

    delete = client.delete(f"/api/consumption/plate/{event_id}", headers=h)
    assert delete.status_code == 204, delete.text

    # Stock restored.
    line = client.get(f"/api/inventory?location_id={loc}", headers=h).json()["items"][0]
    assert Decimal(str(line["quantity"])) == Decimal("5")

    # Calorie total zeroed and the entry hidden (not shown as a stray -120 row).
    after = client.get(f"/api/consumption/calories?profile_id={profile}&on={today}", headers=h).json()
    assert Decimal(str(after["total_calories"])) == Decimal("0")
    assert after["entries"] == []


def test_deleting_an_off_pantry_plate_zeroes_the_total_without_touching_inventory(client: httpx.Client) -> None:
    h = _admin_headers(client)
    profile = _profile(client, h, name="Sam")
    item_id = _item(client, h, name="Gas Station Snack", calories_per_unit=250)

    hp = {**h, "X-Household-Profile": profile}
    plate = client.post("/api/consumption/plate", json={"item_id": item_id, "quantity": 1, "unit": "each"}, headers=hp)
    assert plate.status_code == 200, plate.text
    event_id = plate.json()["event_id"]

    delete = client.delete(f"/api/consumption/plate/{event_id}", headers=h)
    assert delete.status_code == 204, delete.text

    today = datetime.now(UTC).date().isoformat()
    after = client.get(f"/api/consumption/calories?profile_id={profile}&on={today}", headers=h).json()
    assert Decimal(str(after["total_calories"])) == Decimal("0")
    assert after["entries"] == []


def test_deleting_twice_400s(client: httpx.Client) -> None:
    h = _admin_headers(client)
    profile = _profile(client, h, name="Jo")
    item_id = _item(client, h, name="Cracker", calories_per_unit=10)
    hp = {**h, "X-Household-Profile": profile}
    plate = client.post("/api/consumption/plate", json={"item_id": item_id, "quantity": 1, "unit": "each"}, headers=hp)
    event_id = plate.json()["event_id"]

    assert client.delete(f"/api/consumption/plate/{event_id}", headers=h).status_code == 204
    second = client.delete(f"/api/consumption/plate/{event_id}", headers=h)
    assert second.status_code == 400


def test_deleting_unknown_event_404s(client: httpx.Client) -> None:
    h = _admin_headers(client)
    resp = client.delete("/api/consumption/plate/00000000-0000-0000-0000-000000000000", headers=h)
    assert resp.status_code == 404


def test_deleting_a_correction_itself_400s(client: httpx.Client) -> None:
    """A deletion's own compensating entry was never a plate — nothing to undo."""
    h = _admin_headers(client)
    profile = _profile(client, h, name="Robin")
    item_id = _item(client, h, name="Yogurt", calories_per_unit=150)
    hp = {**h, "X-Household-Profile": profile}
    plate = client.post("/api/consumption/plate", json={"item_id": item_id, "quantity": 1, "unit": "each"}, headers=hp)
    event_id = plate.json()["event_id"]
    assert client.delete(f"/api/consumption/plate/{event_id}", headers=h).status_code == 204

    # Find the compensating "deleted" event via the raw events log.
    events = client.get("/api/inventory/events", headers=h).json()["events"]
    correction = next(e for e in events if e.get("item_id") == item_id and e.get("reason") == "deleted")
    resp = client.delete(f"/api/consumption/plate/{correction['event_id']}", headers=h)
    assert resp.status_code == 400
