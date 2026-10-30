"""The inventory API is event-sourced live — ADR 0002 step 1 (make the ledger real).

Drives the real endpoints in-process and asserts that every mutation appends an
immutable ``InventoryEvent``, that reads come from the ``inv_balance`` projection,
that the projection matches the dual-written legacy ``inventory`` table (parity),
that a full rebuild reproduces the same balances (idempotence), and that
lots/expiry stay optional (an intake with neither still records stock).
"""

from __future__ import annotations

import uuid
from decimal import Decimal

import httpx
from sqlalchemy import select

from app.db.session import SessionLocal
from app.models import Inventory
from modules.inventory.models import InventoryBalance, InventoryEvent
from modules.inventory.projector import rebuild_projections


def _headers(client: httpx.Client) -> dict[str, str]:
    client.post("/api/auth/bootstrap-admin", json={"email": "admin@pantryos.local", "password": "TestPass123!"})
    login = client.post("/api/auth/login", json={"email": "admin@pantryos.local", "password": "TestPass123!"})
    assert login.status_code == 200
    return {"Authorization": f"Bearer {login.json()['token']}"}


def _make_item_and_locations(client: httpx.Client, headers: dict[str, str]) -> tuple[str, str, str]:
    item = client.post("/api/items", json={"name": "Ledger Milk"}, headers=headers)
    assert item.status_code == 201
    pantry = client.post("/api/locations", json={"name": "Ledger Pantry"}, headers=headers)
    fridge = client.post("/api/locations", json={"name": "Ledger Fridge"}, headers=headers)
    return item.json()["id"], pantry.json()["id"], fridge.json()["id"]


def test_mutations_write_events_and_reads_come_from_projection(client: httpx.Client) -> None:
    headers = _headers(client)
    item_id, pantry, fridge = _make_item_and_locations(client, headers)

    client.post(
        "/api/inventory/add",
        json={"item_id": item_id, "location_id": pantry, "quantity": 10, "unit": "each"},
        headers=headers,
    )
    client.post(
        "/api/inventory/remove",
        json={"item_id": item_id, "location_id": pantry, "quantity": 3, "unit": "each"},
        headers=headers,
    )
    client.post(
        "/api/inventory/move",
        json={
            "item_id": item_id,
            "source_location_id": pantry,
            "target_location_id": fridge,
            "quantity": 4,
            "unit": "each",
        },
        headers=headers,
    )
    client.post(
        "/api/inventory/adjust",
        json={"item_id": item_id, "location_id": fridge, "quantity": 2, "unit": "each"},
        headers=headers,
    )

    # Reads (GET /inventory) reflect the folded balances: pantry 10-3-4=3, fridge 4→2.
    pantry_qty = client.get(f"/api/inventory?location_id={pantry}", headers=headers).json()["items"][0]["quantity"]
    fridge_qty = client.get(f"/api/inventory?location_id={fridge}", headers=headers).json()["items"][0]["quantity"]
    assert Decimal(str(pantry_qty)) == Decimal("3")
    assert Decimal(str(fridge_qty)) == Decimal("2")

    with SessionLocal() as db:
        events = db.execute(select(InventoryEvent).order_by(InventoryEvent.seq)).scalars().all()
        # add→purchased, remove→consumed, move→transferred, adjust→adjusted (delta -2).
        assert [e.event_type for e in events] == ["purchased", "consumed", "transferred", "adjusted"]
        assert all(e.source == "api" for e in events)

        # Parity: the event-derived projection matches the dual-written legacy table.
        legacy = {(r.item_id, r.location_id): Decimal(r.quantity) for r in db.execute(select(Inventory)).scalars()}
        projected = {
            (r.item_id, r.location_id): Decimal(r.quantity)
            for r in db.execute(select(InventoryBalance)).scalars()
        }
        for key, qty in projected.items():
            assert legacy.get(key) == qty


def test_events_endpoint_returns_authoritative_history(client: httpx.Client) -> None:
    headers = _headers(client)
    item_id, pantry, _fridge = _make_item_and_locations(client, headers)

    client.post(
        "/api/inventory/add",
        json={"item_id": item_id, "location_id": pantry, "quantity": 5, "unit": "each"},
        headers=headers,
    )
    client.post(
        "/api/inventory/remove",
        json={"item_id": item_id, "location_id": pantry, "quantity": 2, "unit": "each"},
        headers=headers,
    )

    resp = client.get(f"/api/inventory/events?location_id={pantry}", headers=headers)
    assert resp.status_code == 200
    events = resp.json()["events"]
    # Newest first.
    assert [e["event_type"] for e in events] == ["consumed", "purchased"]
    assert events[0]["item_name"] == "Ledger Milk"
    assert events[0]["seq"] > events[1]["seq"]


def test_full_rebuild_reproduces_live_balances(client: httpx.Client) -> None:
    headers = _headers(client)
    item_id, pantry, _fridge = _make_item_and_locations(client, headers)
    for qty in (7, 2, 1):
        client.post(
            "/api/inventory/add",
            json={"item_id": item_id, "location_id": pantry, "quantity": qty, "unit": "each"},
            headers=headers,
        )

    with SessionLocal() as db:
        before = {
            (r.item_id, r.location_id): Decimal(r.quantity)
            for r in db.execute(select(InventoryBalance)).scalars()
        }
        rebuild_projections(db)
        db.commit()
        after = {
            (r.item_id, r.location_id): Decimal(r.quantity)
            for r in db.execute(select(InventoryBalance)).scalars()
        }
    assert before == after
    assert before[(uuid.UUID(item_id), uuid.UUID(pantry))] == Decimal("10")


def test_lots_and_expiry_stay_optional(client: httpx.Client) -> None:
    headers = _headers(client)
    item_id, pantry, _fridge = _make_item_and_locations(client, headers)

    # Intake with NO expiry and NO lot — the zero-friction default path.
    plain = client.post(
        "/api/inventory/add",
        json={"item_id": item_id, "location_id": pantry, "quantity": 6, "unit": "each"},
        headers=headers,
    )
    assert plain.status_code == 200
    line = client.get(f"/api/inventory?location_id={pantry}", headers=headers).json()["items"][0]
    assert Decimal(str(line["quantity"])) == Decimal("6")
    assert line["expiration_date"] is None  # nothing was forced

    # A later dated intake surfaces as the soonest-lot expiry on the line.
    client.post(
        "/api/inventory/add",
        json={
            "item_id": item_id,
            "location_id": pantry,
            "quantity": 2,
            "unit": "each",
            "expiration_date": "2030-01-01",
        },
        headers=headers,
    )
    line = client.get(f"/api/inventory?location_id={pantry}", headers=headers).json()["items"][0]
    assert Decimal(str(line["quantity"])) == Decimal("8")
    assert line["expiration_date"] == "2030-01-01"
