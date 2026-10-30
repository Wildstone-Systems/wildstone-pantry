"""The expiring feed as an actionable worklist — ADR 0023 D10, D13, D4.

Two things this pins. First, each row now names the container it is about, so a
"used it" / "threw it out" tap can target that lot instead of falling back to
FEFO and hitting the wrong one. Second, the feed stops nagging about lots whose
clock the household has deliberately stopped — frozen stock and items whose
best-by is meaningless. A list that cries wolf gets ignored wholesale, which
costs more than the rows it surfaces.
"""

from __future__ import annotations

from datetime import date, timedelta

import httpx


def _headers(client: httpx.Client) -> dict[str, str]:
    client.post("/api/auth/bootstrap-admin", json={"email": "admin@pantryos.local", "password": "TestPass123!"})
    login = client.post("/api/auth/login", json={"email": "admin@pantryos.local", "password": "TestPass123!"})
    return {"Authorization": f"Bearer {login.json()['token']}"}


def _item(client, h, name) -> str:
    return client.post("/api/items", json={"name": name}, headers=h).json()["id"]


def _loc(client, h, name) -> str:
    return client.post("/api/locations", json={"name": name}, headers=h).json()["id"]


def _add_dated(client, h, item_id, loc, days_out=2, qty=2):
    resp = client.post(
        "/api/inventory/add",
        json={
            "item_id": item_id,
            "location_id": loc,
            "quantity": qty,
            "unit": "each",
            "expiration_date": (date.today() + timedelta(days=days_out)).isoformat(),
        },
        headers=h,
    )
    assert resp.status_code == 200, resp.text


def _expiring(client, h, loc):
    resp = client.get(f"/api/insights/expiring/{loc}?within_days=7", headers=h)
    assert resp.status_code == 200, resp.text
    return resp.json()["expiring_items"]


def test_rows_name_the_container_and_the_amount(client: httpx.Client) -> None:
    h = _headers(client)
    item = _item(client, h, "Cream")
    fridge = _loc(client, h, "Fridge")
    _add_dated(client, h, item, fridge, days_out=2, qty=2)

    row = _expiring(client, h, fridge)[0]
    assert row["lot_id"] is not None
    assert float(row["quantity_remaining"]) == 2.0
    assert row["unit"]


def test_a_row_can_be_acted_on_directly(client: httpx.Client) -> None:
    """The point of carrying lot_id: the row's own button finishes that lot."""
    h = _headers(client)
    item = _item(client, h, "Cream")
    fridge = _loc(client, h, "Fridge")
    _add_dated(client, h, item, fridge, days_out=1, qty=2)

    row = _expiring(client, h, fridge)[0]
    resp = client.post(f"/api/inventory/lots/{row['lot_id']}/finish", headers=h)
    assert resp.status_code == 200, resp.text
    assert resp.json()["transaction_id"] is not None

    assert _expiring(client, h, fridge) == []  # cleared off the worklist


def test_frozen_stock_drops_off_the_feed(client: httpx.Client) -> None:
    """Freezing is how a household says "stop the clock"."""
    h = _headers(client)
    item = _item(client, h, "Chicken")
    freezer = _loc(client, h, "Freezer")
    _add_dated(client, h, item, freezer, days_out=3)

    row = _expiring(client, h, freezer)[0]
    resp = client.post(f"/api/inventory/lots/{row['lot_id']}/freeze", headers=h)
    assert resp.status_code == 200, resp.text

    assert _expiring(client, h, freezer) == []


def test_thawing_puts_it_back_on_the_feed(client: httpx.Client) -> None:
    h = _headers(client)
    item = _item(client, h, "Chicken")
    freezer = _loc(client, h, "Freezer")
    _add_dated(client, h, item, freezer, days_out=3)

    lot_id = _expiring(client, h, freezer)[0]["lot_id"]
    client.post(f"/api/inventory/lots/{lot_id}/freeze", headers=h)
    assert _expiring(client, h, freezer) == []

    client.post(f"/api/inventory/lots/{lot_id}/thaw", headers=h)
    assert len(_expiring(client, h, freezer)) == 1


def test_ignore_expiry_suppresses_the_item(client: httpx.Client) -> None:
    """Spices and condiments are used long past their printed date; nagging
    about them every few days trains people to ignore the list (ADR 0023 D4)."""
    h = _headers(client)
    paprika = _item(client, h, "Paprika")
    pantry = _loc(client, h, "Pantry")
    _add_dated(client, h, paprika, pantry, days_out=1)
    assert len(_expiring(client, h, pantry)) == 1

    resp = client.put(f"/api/items/{paprika}", json={"ignore_expiry": True}, headers=h)
    assert resp.status_code == 200, resp.text
    assert resp.json()["ignore_expiry"] is True

    assert _expiring(client, h, pantry) == []


def test_ignore_expiry_is_reversible(client: httpx.Client) -> None:
    h = _headers(client)
    paprika = _item(client, h, "Paprika")
    pantry = _loc(client, h, "Pantry")
    _add_dated(client, h, paprika, pantry, days_out=1)

    client.put(f"/api/items/{paprika}", json={"ignore_expiry": True}, headers=h)
    assert _expiring(client, h, pantry) == []

    client.put(f"/api/items/{paprika}", json={"ignore_expiry": False}, headers=h)
    assert len(_expiring(client, h, pantry)) == 1


def test_ignoring_one_item_leaves_the_others_alone(client: httpx.Client) -> None:
    h = _headers(client)
    paprika = _item(client, h, "Paprika")
    cream = _item(client, h, "Cream")
    pantry = _loc(client, h, "Pantry")
    _add_dated(client, h, paprika, pantry, days_out=1)
    _add_dated(client, h, cream, pantry, days_out=2)

    client.put(f"/api/items/{paprika}", json={"ignore_expiry": True}, headers=h)

    rows = _expiring(client, h, pantry)
    assert [r["item_name"] for r in rows] == ["Cream"]
