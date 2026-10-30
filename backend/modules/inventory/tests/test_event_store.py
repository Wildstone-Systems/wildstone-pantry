"""Event-store persistence + replay parity on the SQLite harness — ADR 0002 step 1.

Confirms the new tables work end-to-end (seq autoincrements, rows persist) and
that replaying the persisted events through the pure balance fold reproduces the
expected balance. Nothing in the app reads/writes these yet — this is the
additive first step.
"""

from __future__ import annotations

from decimal import Decimal

from sqlalchemy import select

from app.db.session import SessionLocal
from app.models import InventoryLocation, Item
from modules.inventory.models import InventoryEvent
from modules.inventory.projections import EventData, replay_balances


def _seed_item_location(db):
    item = Item(name="Milk")
    location = InventoryLocation(name="Pantry")
    db.add_all([item, location])
    db.flush()
    return item, location


def test_event_store_persists_and_orders_by_seq():
    with SessionLocal() as db:
        item, location = _seed_item_location(db)
        db.add_all(
            [
                InventoryEvent(event_type="purchased", item_id=item.id, location_id=location.id, quantity=Decimal("10")),
                InventoryEvent(event_type="consumed", item_id=item.id, location_id=location.id, quantity=Decimal("4")),
            ]
        )
        db.commit()

        rows = db.execute(select(InventoryEvent).order_by(InventoryEvent.seq)).scalars().all()
        assert [r.event_type for r in rows] == ["purchased", "consumed"]
        # seq is a monotonic autoincrement assigned by the DB.
        assert rows[0].seq < rows[1].seq
        assert all(r.event_id is not None for r in rows)


def test_replay_persisted_events_matches_expected_balance():
    with SessionLocal() as db:
        item, location = _seed_item_location(db)
        db.add_all(
            [
                InventoryEvent(event_type="purchased", item_id=item.id, location_id=location.id, quantity=Decimal("10")),
                InventoryEvent(event_type="consumed", item_id=item.id, location_id=location.id, quantity=Decimal("3")),
                InventoryEvent(event_type="adjusted", item_id=item.id, location_id=location.id, quantity=Decimal("-2")),
            ]
        )
        db.commit()

        rows = db.execute(select(InventoryEvent).order_by(InventoryEvent.seq)).scalars().all()
        events = [
            EventData(
                event_type=r.event_type,
                item_id=r.item_id,
                location_id=r.location_id,
                quantity=Decimal(r.quantity),
                unit=r.unit,
                seq=r.seq,
                target_location_id=r.target_location_id,
                lot_id=r.lot_id,
            )
            for r in rows
        ]
        balances = replay_balances(events)
        assert balances[(item.id, location.id)] == Decimal("5")
