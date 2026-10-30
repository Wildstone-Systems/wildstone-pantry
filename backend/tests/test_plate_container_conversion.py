"""Plating a weight amount from an item stocked in whole "each" units —
regression for the real bug report.

`plate()` used to write the eaten quantity/unit straight onto the flat
Inventory row and the ledger event without converting to the item's stock
unit first. For an item tracked as 2 whole "each" bags (a package-size hint
of 340 g each), plating "170 g" of it compared grams against a container
count (`2 each < 170` → a bogus "Insufficient inventory quantity"), and if it
had passed anyway would have written a gram delta straight onto an "each"
balance. `remove_inventory` already converts into the stock unit before
touching anything — `plate` needed the same treatment: draw down a fraction
of an "each" (170 / 340 = 0.5), while still reporting/logging the *eaten*
amount (170 g) back to the caller and the calorie rollup.
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


def test_plating_grams_from_an_each_stocked_item_draws_a_fraction_of_a_unit(client: httpx.Client) -> None:
    h = _admin_headers(client)
    profile = client.post("/api/household/profiles", json={"display_name": "Alex"}, headers=h).json()["id"]

    # A 340g bag of chocolate chips, tracked in whole "each" bags — same shape
    # as a real pantry item (package weight known, stocked by count).
    item = client.post(
        "/api/items",
        json={"name": "Chocolate Chips", "stock_mode": "counted", "size_quantity": 340, "size_unit": "g"},
        headers=h,
    ).json()
    assert item["base_unit"] == "each"
    loc = client.post("/api/locations", json={"name": "Pantry"}, headers=h).json()["id"]

    # Plain intake — no container batch metadata, exactly how a "manual add"
    # to the pantry records it in practice.
    r = client.post(
        "/api/inventory/add",
        json={"item_id": item["id"], "location_id": loc, "quantity": 2, "unit": "each"},
        headers=h,
    )
    assert r.status_code == 200, r.text

    # Eat 170g — half a bag — attributed to Alex.
    hp = {**h, "X-Household-Profile": profile}
    plate = client.post(
        "/api/consumption/plate",
        json={"item_id": item["id"], "location_id": loc, "quantity": 170, "unit": "g"},
        headers=hp,
    )
    assert plate.status_code == 200, plate.text
    body = plate.json()
    # The response still reports what was actually eaten, not the stock unit.
    assert Decimal(str(body["quantity"])) == Decimal("170")
    assert body["unit"] == "g"
    assert Decimal(str(body["balance"])) == Decimal("1.5")  # 2 each - 0.5 each

    # Balance in the projection-backed read agrees.
    line = client.get(f"/api/inventory?location_id={loc}", headers=h).json()["items"][0]
    assert Decimal(str(line["quantity"])) == Decimal("1.5")
    assert line["unit"] == "each"

    # The calorie-log entry still shows the eaten amount (grams), not the
    # 0.5-each stock-unit fraction that actually moved.
    today = datetime.now(UTC).date().isoformat()
    rollup = client.get(f"/api/consumption/calories?profile_id={profile}&on={today}", headers=h).json()
    entry = rollup["entries"][0]
    assert Decimal(str(entry["quantity"])) == Decimal("170")
    assert entry["unit"] == "g"
