"""The tracking worklist — ADR 0023 D8.

What someone should act on right now, ranked by urgency rather than by which
feed produced it. The ordering is the product: a list where the first rows are
not the ones that matter gets skimmed and then ignored.
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


def _add(client, h, item_id, loc, qty=2, days_out=None, **extra):
    body = {"item_id": item_id, "location_id": loc, "quantity": qty, "unit": "each", **extra}
    if days_out is not None:
        body["expiration_date"] = (date.today() + timedelta(days=days_out)).isoformat()
    resp = client.post("/api/inventory/add", json=body, headers=h)
    assert resp.status_code == 200, resp.text


def _worklist(client, h, loc):
    resp = client.get(f"/api/inventory/worklist?location_id={loc}", headers=h)
    assert resp.status_code == 200, resp.text
    return resp.json()["rows"]


def test_expiring_stock_leads_the_list(client: httpx.Client) -> None:
    h = _headers(client)
    fridge = _loc(client, h, "Fridge")
    staple = _item(client, h, "Rice")
    milk = _item(client, h, "Milk")
    _add(client, h, staple, fridge, qty=5)  # no date at all
    _add(client, h, milk, fridge, qty=1, days_out=1)

    rows = _worklist(client, h, fridge)
    assert rows[0]["item_name"] == "Milk"
    assert rows[0]["source"] == "expiring"
    assert rows[0]["days_until_expiration"] == 1


def test_already_expired_outranks_merely_soon(client: httpx.Client) -> None:
    h = _headers(client)
    fridge = _loc(client, h, "Fridge")
    soon = _item(client, h, "Yogurt")
    gone = _item(client, h, "Cream")
    _add(client, h, soon, fridge, days_out=3)
    _add(client, h, gone, fridge, days_out=-2)

    rows = _worklist(client, h, fridge)
    assert [r["item_name"] for r in rows[:2]] == ["Cream", "Yogurt"]


def test_open_containers_surface_without_a_date(client: httpx.Client) -> None:
    """The yogurt case: a started tub is what someone is most likely to finish
    next, deadline or no deadline."""
    h = _headers(client)
    fridge = _loc(client, h, "Fridge")
    yogurt = _item(client, h, "Yogurt")
    _add(client, h, yogurt, fridge, qty=3, sealed_count=3, container_size=1, container_unit="qt")

    lots = client.get(f"/api/inventory/lots?item_id={yogurt}&location_id={fridge}", headers=h).json()["lots"]
    client.post(f"/api/inventory/lots/{lots[0]['lot_id']}/open", headers=h)

    rows = _worklist(client, h, fridge)
    opened = [r for r in rows if r["source"] == "open"]
    assert len(opened) == 1
    assert opened[0]["lot_id"] is not None
    assert opened[0]["status"] == "open"


def test_a_row_carries_what_its_buttons_need(client: httpx.Client) -> None:
    h = _headers(client)
    fridge = _loc(client, h, "Fridge")
    milk = _item(client, h, "Milk")
    _add(client, h, milk, fridge, qty=1, days_out=2)

    row = _worklist(client, h, fridge)[0]
    assert row["lot_id"] and row["unit"] and float(row["quantity_remaining"]) > 0

    # Finish it straight off the row — no lookup, no second call.
    resp = client.post(f"/api/inventory/lots/{row['lot_id']}/finish", headers=h)
    assert resp.status_code == 200, resp.text
    assert not [r for r in _worklist(client, h, fridge) if r["lot_id"] == row["lot_id"]]


def test_recently_consumed_items_surface_even_with_no_date(client: httpx.Client) -> None:
    """The stated problem is ordinary usage going unrecorded, so the feed cannot
    be built from expiry dates alone."""
    h = _headers(client)
    pantry = _loc(client, h, "Pantry")
    coffee = _item(client, h, "Coffee")
    _add(client, h, coffee, pantry, qty=10)

    # Used a few times lately, nothing expiring.
    for _ in range(3):
        client.post(
            "/api/inventory/remove",
            json={"item_id": coffee, "location_id": pantry, "quantity": 1, "unit": "each"},
            headers=h,
        )

    rows = _worklist(client, h, pantry)
    assert any(r["item_name"] == "Coffee" and r["source"] == "recent" for r in rows)


def test_an_item_appears_once_under_its_most_urgent_reason(client: httpx.Client) -> None:
    h = _headers(client)
    fridge = _loc(client, h, "Fridge")
    yogurt = _item(client, h, "Yogurt")
    _add(client, h, yogurt, fridge, qty=2, days_out=2)
    client.post(
        "/api/inventory/remove",
        json={"item_id": yogurt, "location_id": fridge, "quantity": 1, "unit": "each"},
        headers=h,
    )

    rows = _worklist(client, h, fridge)
    yogurt_rows = [r for r in rows if r["item_name"] == "Yogurt"]
    assert len(yogurt_rows) == 1
    assert yogurt_rows[0]["source"] == "expiring"


def test_frozen_and_ignored_stay_off_the_list(client: httpx.Client) -> None:
    h = _headers(client)
    freezer = _loc(client, h, "Freezer")
    chicken = _item(client, h, "Chicken")
    paprika = _item(client, h, "Paprika")
    _add(client, h, chicken, freezer, days_out=2)
    _add(client, h, paprika, freezer, days_out=2)

    rows = _worklist(client, h, freezer)
    chicken_lot = next(r["lot_id"] for r in rows if r["item_name"] == "Chicken")
    client.post(f"/api/inventory/lots/{chicken_lot}/freeze", headers=h)
    client.put(f"/api/items/{paprika}", json={"ignore_expiry": True}, headers=h)

    names = [r["item_name"] for r in _worklist(client, h, freezer)]
    assert "Chicken" not in names
    assert "Paprika" not in names


def test_unknown_location_is_a_404(client: httpx.Client) -> None:
    h = _headers(client)
    resp = client.get("/api/inventory/worklist?location_id=00000000-0000-0000-0000-000000000000", headers=h)
    assert resp.status_code == 404
