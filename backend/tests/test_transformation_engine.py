"""Transformation engine (step 4a, ADR 0003 D3): cooking consumes input lots AND
produces a tracked output product on the inventory ledger via a `produced` event."""

from __future__ import annotations

import uuid
from decimal import Decimal

import httpx

from app.db import SessionLocal
from modules.inventory.domain.lots import replay_lots
from modules.inventory.projections.event_data import EventData
from modules.inventory.projector import rebuild_projections


def _headers(client: httpx.Client) -> dict[str, str]:
    client.post("/api/auth/bootstrap-admin", json={"email": "admin@pantryos.local", "password": "TestPass123!"})
    login = client.post("/api/auth/login", json={"email": "admin@pantryos.local", "password": "TestPass123!"})
    return {"Authorization": f"Bearer {login.json()['token']}"}


def _balance(client: httpx.Client, h: dict[str, str], loc: str, item_name: str) -> Decimal | None:
    rows = {r["item_name"]: r["quantity"] for r in client.get(f"/api/inventory?location_id={loc}", headers=h).json()["items"]}
    return None if item_name not in rows else Decimal(str(rows[item_name]))


def test_cooking_produces_output_lot_and_consumes_inputs(client: httpx.Client) -> None:
    h = _headers(client)
    profile = client.post("/api/household/profiles", json={"display_name": "Sam"}, headers=h).json()["id"]
    hp = {**h, "X-Household-Profile": profile}

    loc = client.post("/api/locations", json={"name": "Kitchen"}, headers=h).json()["id"]
    flour = client.post("/api/items", json={"name": "Flour"}, headers=h).json()["id"]
    bread = client.post("/api/items", json={"name": "Bread"}, headers=h).json()["id"]

    client.post(
        "/api/inventory/add",
        json={"item_id": flour, "location_id": loc, "quantity": 10, "unit": "cup"},
        headers=h,
    )

    # A recipe that turns 3 cups of flour into 1 loaf of bread.
    recipe = client.post(
        "/api/recipes",
        json={
            "name": "Simple Bread",
            "instructions": "Mix and bake",
            "output_item_id": bread,
            "output_quantity": 1,
            "output_unit": "loaf",
        },
        headers=h,
    ).json()
    assert recipe["output_item_id"] == bread
    recipe_id = recipe["id"]

    client.put(
        f"/api/recipes/{recipe_id}/ingredients",
        json=[{"item_id": flour, "quantity_required": 3, "unit": "cup"}],
        headers=h,
    )

    cook = client.post(
        f"/api/recipes/{recipe_id}/cook",
        json={"location_id": loc, "servings": 2, "output_weight_grams": 850},
        headers=hp,
    )
    assert cook.status_code == 200
    body = cook.json()
    assert body["produced_item_id"] == bread
    # output_quantity (1) * servings (2) = 2 loaves.
    assert Decimal(str(body["produced_quantity"])) == Decimal("2")
    assert Decimal(str(body["produced_balance"])) == Decimal("2")

    # Inputs consumed (10 - 3*2 = 4), output balance up by 2.
    assert _balance(client, hp, loc, "Flour") == Decimal("4")
    assert _balance(client, hp, loc, "Bread") == Decimal("2")

    # An output lot exists carrying the produced quantity.
    lots = client.get(f"/api/inventory/events?location_id={loc}", headers=h).json()["events"]
    produced = [e for e in lots if e["event_type"] == "produced"]
    assert len(produced) == 1
    assert produced[0]["actor"] == profile
    assert Decimal(str(produced[0]["quantity"])) == Decimal("2")

    # consumed + produced both appear in the ledger, attributed to the cook.
    kinds = [e["event_type"] for e in lots]
    assert "consumed" in kinds and "produced" in kinds
    cook_events = [e for e in lots if e["event_type"] in ("consumed", "produced")]
    assert all(e["actor"] == profile for e in cook_events)


def test_rebuild_reproduces_produced_balances(client: httpx.Client) -> None:
    h = _headers(client)
    loc = client.post("/api/locations", json={"name": "Bakery"}, headers=h).json()["id"]
    flour = client.post("/api/items", json={"name": "Flour"}, headers=h).json()["id"]
    bread = client.post("/api/items", json={"name": "Bread"}, headers=h).json()["id"]

    client.post(
        "/api/inventory/add",
        json={"item_id": flour, "location_id": loc, "quantity": 6, "unit": "cup"},
        headers=h,
    )
    recipe_id = client.post(
        "/api/recipes",
        json={"name": "Loaf", "output_item_id": bread, "output_quantity": 2, "output_unit": "loaf"},
        headers=h,
    ).json()["id"]
    client.put(
        f"/api/recipes/{recipe_id}/ingredients",
        json=[{"item_id": flour, "quantity_required": 3, "unit": "cup"}],
        headers=h,
    )
    client.post(f"/api/recipes/{recipe_id}/cook", json={"location_id": loc, "servings": 1}, headers=h)

    before = _balance(client, h, loc, "Bread")
    assert before == Decimal("2")

    # A full replay of the log must reproduce the same projected balances.
    db = SessionLocal()
    try:
        rebuild_projections(db)
        db.commit()
    finally:
        db.close()

    assert _balance(client, h, loc, "Bread") == before
    assert _balance(client, h, loc, "Flour") == Decimal("3")


def test_cooking_without_output_is_backward_compatible(client: httpx.Client) -> None:
    h = _headers(client)
    loc = client.post("/api/locations", json={"name": "Galley"}, headers=h).json()["id"]
    egg = client.post("/api/items", json={"name": "Egg"}, headers=h).json()["id"]

    client.post(
        "/api/inventory/add",
        json={"item_id": egg, "location_id": loc, "quantity": 6, "unit": "each"},
        headers=h,
    )
    recipe_id = client.post("/api/recipes", json={"name": "Scramble"}, headers=h).json()["id"]
    client.put(
        f"/api/recipes/{recipe_id}/ingredients",
        json=[{"item_id": egg, "quantity_required": 2, "unit": "each"}],
        headers=h,
    )
    cook = client.post(f"/api/recipes/{recipe_id}/cook", json={"location_id": loc, "servings": 1}, headers=h)
    assert cook.status_code == 200
    assert cook.json().get("produced_item_id") is None
    assert _balance(client, h, loc, "Egg") == Decimal("4")

    # No produced event was written.
    events = client.get(f"/api/inventory/events?location_id={loc}", headers=h).json()["events"]
    assert all(e["event_type"] != "produced" for e in events)


def test_replay_lots_creates_a_lot_for_a_produced_event() -> None:
    item_id = uuid.uuid4()
    location_id = uuid.uuid4()
    events = [
        EventData(
            event_type="produced",
            item_id=item_id,
            location_id=location_id,
            quantity=Decimal("3"),
            unit="loaf",
            seq=1,
            lot_id=uuid.uuid4(),
        )
    ]
    lots = replay_lots(events)
    assert len(lots) == 1
    lot = next(iter(lots.values()))
    assert lot.item_id == item_id
    assert lot.location_id == location_id
    assert lot.quantity_remaining == Decimal("3")
