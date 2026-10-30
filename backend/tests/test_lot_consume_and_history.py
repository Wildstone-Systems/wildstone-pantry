"""Using a container up, and seeing that it happened.

Three gaps this covers, all from the same workflow: you finish a pot of yogurt
without logging a meal, you want it off the shelf, and you want to still see
tomorrow that it went.
"""

from __future__ import annotations

import uuid

import pytest


def _setup(client, *, name="Yogurt", qty=2, unit="each"):
    loc = client.post("/api/locations", json={"name": f"Fridge {uuid.uuid4().hex[:6]}"}).json()
    item = client.post("/api/items", json={"name": f"{name} {uuid.uuid4().hex[:6]}"}).json()
    client.post(
        "/api/inventory/add",
        json={"item_id": item["id"], "location_id": loc["id"], "quantity": qty, "unit": unit},
    )
    return loc, item


def _lots(client, item_id, location_id):
    return client.get(
        "/api/inventory/lots", params={"item_id": item_id, "location_id": location_id}
    ).json()["lots"]


def _event_types(client, item_id):
    rows = client.get("/api/inventory/events", params={"limit": 200}).json()["events"]
    return [r["event_type"] for r in rows if r["item_id"] == item_id]


def test_using_a_container_up_is_consumption_not_waste(client):
    """The distinction that matters: Spoil counts it as waste and inflates the
    spoilage figures with food that was eaten; Remove claims it was never there
    and erases it from consumption trends."""
    loc, item = _setup(client)
    lot = _lots(client, item["id"], loc["id"])[0]

    res = client.post(f"/api/inventory/lots/{lot['lot_id']}/finish")
    assert res.status_code == 200, res.text

    assert "consumed" in _event_types(client, item["id"])
    assert "spoiled" not in _event_types(client, item["id"])


def test_using_up_one_sealed_container_leaves_the_rest_of_the_batch(client):
    """Takes ONE container of a sealed batch, not the whole batch — finishing
    the yogurt pot you opened must not empty the other three (ADR 0014).
    Deliberately the same rule Spoil already follows."""
    loc = client.post("/api/locations", json={"name": f"Fridge {uuid.uuid4().hex[:6]}"}).json()
    item = client.post("/api/items", json={"name": f"Yogurt {uuid.uuid4().hex[:6]}"}).json()
    client.post(
        "/api/inventory/add",
        json={
            "item_id": item["id"],
            "location_id": loc["id"],
            "quantity": 4,
            "unit": "each",
            "sealed_count": 4,
            "container_size": 1,
            "container_unit": "each",
        },
    )
    lot = _lots(client, item["id"], loc["id"])[0]
    before = float(lot["quantity_remaining"])
    assert before == 4

    client.post(f"/api/inventory/lots/{lot['lot_id']}/finish")

    after = _lots(client, item["id"], loc["id"])
    remaining = sum(float(r["quantity_remaining"]) for r in after)
    assert remaining == 3, f"one pot should have gone, not the batch (left {remaining})"


def test_finishing_an_already_empty_lot_changes_nothing(client):
    """Originally written to expect a refusal. ADR 0023 D13 settled it the other
    way: two people can reach for the same empty container, and the second tap
    should not read as an error when the intent is already satisfied.

    What actually matters is preserved and asserted here — the repeat call must
    not fall through and consume a DIFFERENT container."""
    loc, item = _setup(client, qty=1)
    lot = _lots(client, item["id"], loc["id"])[0]
    client.post(f"/api/inventory/lots/{lot['lot_id']}/finish")
    after_first = sum(float(r["quantity_remaining"]) for r in _lots(client, item["id"], loc["id"]))

    again = client.post(f"/api/inventory/lots/{lot['lot_id']}/finish")
    assert again.status_code == 200, again.text
    assert again.json()["already_resolved"] is True
    assert again.json()["transaction_id"] is None

    after_second = sum(float(r["quantity_remaining"]) for r in _lots(client, item["id"], loc["id"]))
    assert after_second == after_first


def test_consuming_a_lot_that_does_not_exist_is_a_404(client):
    assert client.post(f"/api/inventory/lots/{uuid.uuid4()}/finish").status_code == 404


# --- Scan-out on a part-used package -------------------------------------------


@pytest.fixture()
def part_used(client):
    """A 400g package with only 250g left — the live 'Coleslaw' shape."""
    loc = client.post("/api/locations", json={"name": f"Fridge {uuid.uuid4().hex[:6]}"}).json()
    item = client.post(
        "/api/items",
        json={
            "name": f"Coleslaw {uuid.uuid4().hex[:6]}",
            "size_quantity": 400,
            "size_unit": "g",
            "stock_mode": "measured",
        },
    ).json()
    client.post(
        "/api/inventory/add",
        json={"item_id": item["id"], "location_id": loc["id"], "quantity": 250, "unit": "g"},
    )
    return loc, item


def test_scanning_out_a_part_used_package_finishes_it(client, part_used):
    """One 'each' is a whole 400g package, but only 250g is left. Erroring
    strands the remainder on the books forever; the operator is standing there
    holding the empty tub."""
    loc, item = part_used
    res = client.post(
        "/api/inventory/scan",
        json={
            "item_id": item["id"],
            "location_id": loc["id"],
            "direction": "out",
            "quantity": 1,
        },
    )
    assert res.status_code == 200, res.text

    on_hand = client.get("/api/inventory", params={"location_id": loc["id"]}).json()["items"]
    line = next((r for r in on_hand if r["item_id"] == item["id"]), None)
    assert line is None or float(line["quantity"]) == 0


def test_a_genuine_shortfall_still_says_what_it_could_not_do(client):
    """Asking for more than exists, outside the scan station, must still fail —
    and the message has to name the amounts, or nobody can tell WHY."""
    loc, item = _setup(client, qty=1)

    res = client.post(
        "/api/inventory/remove",
        json={"item_id": item["id"], "location_id": loc["id"], "quantity": 5, "unit": "each"},
    )
    assert res.status_code == 400
    detail = res.json()["detail"]
    assert "5" in detail and "1" in detail, f"message should name the amounts: {detail!r}"
    assert "on hand" in detail.lower()


# --- Persistent history ---------------------------------------------------------


def test_history_survives_and_can_be_windowed(client):
    """The scan station's Recent list was useState — gone on reload. The ledger
    is the durable version of the same thing."""
    loc, item = _setup(client, qty=2)
    lot = _lots(client, item["id"], loc["id"])[0]
    client.post(f"/api/inventory/lots/{lot['lot_id']}/finish")

    recent = client.get(
        "/api/inventory/events",
        params={"since_hours": 24, "event_types": "consumed,spoiled", "limit": 50},
    ).json()["events"]

    mine = [r for r in recent if r["item_id"] == item["id"]]
    assert mine, "a consume from a moment ago must be in the last 24 hours"
    assert all(r["event_type"] in ("consumed", "spoiled") for r in recent), "filter must apply"


def test_the_window_actually_excludes_older_events(client):
    loc, item = _setup(client, qty=2)
    lot = _lots(client, item["id"], loc["id"])[0]
    client.post(f"/api/inventory/lots/{lot['lot_id']}/finish")

    from app.db import SessionLocal
    from modules.inventory.models.event import InventoryEvent

    db = SessionLocal()
    try:
        row = (
            db.query(InventoryEvent)
            .filter_by(item_id=uuid.UUID(item["id"]), event_type="consumed")
            .one()
        )
        from datetime import datetime, timedelta, timezone

        row.occurred_at = datetime.now(timezone.utc) - timedelta(hours=48)
        db.commit()
    finally:
        db.close()

    recent = client.get(
        "/api/inventory/events", params={"since_hours": 24, "limit": 100}
    ).json()["events"]
    # The purchase is still recent, so filter to the event we actually aged.
    consumed = [
        r for r in recent if r["item_id"] == item["id"] and r["event_type"] == "consumed"
    ]
    assert not consumed, "a 48h-old consume is outside a 24h window"
