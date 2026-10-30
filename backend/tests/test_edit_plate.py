"""Editing a mistaken plate's amount — the Calories page's edit button.

Composes delete_plate (undo the original) with a fresh plate() call for the
corrected amount, so the net inventory/calorie effect is exactly as if the
corrected amount had been plated the first time.
"""

from __future__ import annotations

from datetime import UTC, datetime
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


def test_editing_quantity_on_a_pantry_plate_nets_the_correct_stock_and_total(client: httpx.Client) -> None:
    h = _admin_headers(client)
    profile = _profile(client, h, name="Alex")
    item_id = _item(client, h, name="Organic Virgin Coconut Oil", calories_per_unit=120)
    loc = _location(client, h, name="Pantry")
    client.post("/api/inventory/add", json={"item_id": item_id, "location_id": loc, "quantity": 5, "unit": "each"}, headers=h)

    hp = {**h, "X-Household-Profile": profile}
    plate = client.post(
        "/api/consumption/plate", json={"item_id": item_id, "location_id": loc, "quantity": 1, "unit": "each"}, headers=hp
    )
    event_id = plate.json()["event_id"]

    # Actually had 2, not 1.
    edit = client.patch(f"/api/consumption/plate/{event_id}", json={"quantity": 2}, headers=h)
    assert edit.status_code == 200, edit.text
    body = edit.json()
    assert Decimal(str(body["quantity"])) == Decimal("2")
    assert body["unit"] == "each"
    assert Decimal(str(body["calories"])) == Decimal("240")
    new_event_id = body["event_id"]
    assert new_event_id != event_id

    # 5 - 2 = 3, not 5 - 1 - 2 = 2 (the delete+replate composition must net
    # correctly, not double-subtract).
    line = client.get(f"/api/inventory?location_id={loc}", headers=h).json()["items"][0]
    assert Decimal(str(line["quantity"])) == Decimal("3")

    today = datetime.now(UTC).date().isoformat()
    rollup = client.get(f"/api/consumption/calories?profile_id={profile}&on={today}", headers=h).json()
    assert Decimal(str(rollup["total_calories"])) == Decimal("240")
    assert len(rollup["entries"]) == 1
    assert rollup["entries"][0]["event_id"] == new_event_id
    assert Decimal(str(rollup["entries"][0]["quantity"])) == Decimal("2")


def test_editing_only_the_unit_keeps_the_original_quantity_number(client: httpx.Client) -> None:
    h = _admin_headers(client)
    profile = _profile(client, h, name="Sam")
    item_id = client.post(
        "/api/items",
        json={"name": "Butter", "energy_kcal_100g": 717, "serving_size_g": 100},
        headers=h,
    ).json()["id"]

    hp = {**h, "X-Household-Profile": profile}
    plate = client.post("/api/consumption/plate", json={"item_id": item_id, "quantity": 3, "unit": "serving"}, headers=hp)
    assert plate.status_code == 200, plate.text
    event_id = plate.json()["event_id"]

    edit = client.patch(f"/api/consumption/plate/{event_id}", json={"unit": "g"}, headers=h)
    assert edit.status_code == 200, edit.text
    body = edit.json()
    assert Decimal(str(body["quantity"])) == Decimal("3")  # unchanged
    assert body["unit"] == "g"
    assert Decimal(str(body["calories"])) == Decimal("21.51")  # 717 * 3/100


def test_editing_an_off_pantry_plate_does_not_touch_inventory(client: httpx.Client) -> None:
    h = _admin_headers(client)
    profile = _profile(client, h, name="Jo")
    item_id = _item(client, h, name="Gas Station Snack", calories_per_unit=250)
    hp = {**h, "X-Household-Profile": profile}
    plate = client.post("/api/consumption/plate", json={"item_id": item_id, "quantity": 1, "unit": "each"}, headers=hp)
    event_id = plate.json()["event_id"]

    edit = client.patch(f"/api/consumption/plate/{event_id}", json={"quantity": 2}, headers=h)
    assert edit.status_code == 200, edit.text
    assert edit.json()["location_id"] is None
    assert Decimal(str(edit.json()["calories"])) == Decimal("500")


def test_editing_an_already_deleted_entry_400s(client: httpx.Client) -> None:
    h = _admin_headers(client)
    profile = _profile(client, h, name="Robin")
    item_id = _item(client, h, name="Cracker", calories_per_unit=10)
    hp = {**h, "X-Household-Profile": profile}
    plate = client.post("/api/consumption/plate", json={"item_id": item_id, "quantity": 1, "unit": "each"}, headers=hp)
    event_id = plate.json()["event_id"]
    assert client.delete(f"/api/consumption/plate/{event_id}", headers=h).status_code == 204

    edit = client.patch(f"/api/consumption/plate/{event_id}", json={"quantity": 2}, headers=h)
    assert edit.status_code == 400


def test_editing_unknown_event_404s(client: httpx.Client) -> None:
    h = _admin_headers(client)
    resp = client.patch(
        "/api/consumption/plate/00000000-0000-0000-0000-000000000000", json={"quantity": 2}, headers=h
    )
    assert resp.status_code == 404
