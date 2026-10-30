"""Backfill parity tests (ADR 0002 step 2) — on the SQLite harness.

The gate: after backfilling legacy inventory + transactions, the rebuilt
``inv_balance`` projection equals the authoritative ``inventory.quantity`` for
every row — including when the advisory transaction log doesn't sum to the
current balance (reconciliation closes the gap).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import select

from app.db.session import SessionLocal
from app.models import Inventory, InventoryLocation, InventoryTransaction, Item
from modules.inventory.backfill import backfill_from_legacy
from modules.inventory.models import InventoryBalance, InventoryEvent


def _ts(n: int) -> datetime:
    return datetime(2026, 1, 1, tzinfo=UTC) + timedelta(minutes=n)


def _balances(db) -> dict[tuple, Decimal]:
    rows = db.execute(select(InventoryBalance)).scalars().all()
    return {(r.item_id, r.location_id): Decimal(r.quantity) for r in rows}


def test_backfill_matches_legacy_balances_from_transactions():
    with SessionLocal() as db:
        item = Item(name="Milk")
        pantry = InventoryLocation(name="Pantry")
        fridge = InventoryLocation(name="Fridge")
        db.add_all([item, pantry, fridge])
        db.flush()

        # A transaction history that sums exactly to the inventory rows.
        db.add_all(
            [
                InventoryTransaction(item_id=item.id, location_id=pantry.id, quantity_change=Decimal("10"),
                                     unit="each", transaction_type="add", created_at=_ts(1)),
                InventoryTransaction(item_id=item.id, location_id=pantry.id, quantity_change=Decimal("-3"),
                                     unit="each", transaction_type="remove", created_at=_ts(2)),
                InventoryTransaction(item_id=item.id, location_id=pantry.id, quantity_change=Decimal("-2"),
                                     unit="each", transaction_type="move_out", created_at=_ts(3)),
                InventoryTransaction(item_id=item.id, location_id=fridge.id, quantity_change=Decimal("2"),
                                     unit="each", transaction_type="move_in", created_at=_ts(3)),
            ]
        )
        db.add_all(
            [
                Inventory(item_id=item.id, location_id=pantry.id, quantity=Decimal("5"), unit="each"),
                Inventory(item_id=item.id, location_id=fridge.id, quantity=Decimal("2"), unit="each"),
            ]
        )
        db.commit()

        summary = backfill_from_legacy(db)
        db.commit()

        assert summary["reconciled"] == 0  # history already matches
        balances = _balances(db)
        assert balances[(item.id, pantry.id)] == Decimal("5")
        assert balances[(item.id, fridge.id)] == Decimal("2")


def test_backfill_reconciles_drift_to_authoritative_inventory():
    with SessionLocal() as db:
        item = Item(name="Flour")
        pantry = InventoryLocation(name="Pantry")
        db.add_all([item, pantry])
        db.flush()

        # Transactions sum to 4, but the authoritative inventory says 7 (drift).
        db.add_all(
            [
                InventoryTransaction(item_id=item.id, location_id=pantry.id, quantity_change=Decimal("4"),
                                     unit="each", transaction_type="add", created_at=_ts(1)),
            ]
        )
        db.add(Inventory(item_id=item.id, location_id=pantry.id, quantity=Decimal("7"), unit="each"))
        db.commit()

        summary = backfill_from_legacy(db)
        db.commit()

        assert summary["reconciled"] == 1
        assert _balances(db)[(item.id, pantry.id)] == Decimal("7")
        # The reconciliation is recorded as an explicit event, not a silent fix.
        kinds = [r.event_type for r in db.execute(select(InventoryEvent).order_by(InventoryEvent.seq)).scalars()]
        assert kinds == ["purchased", "reconciled"]


def test_backfill_is_idempotent():
    with SessionLocal() as db:
        item = Item(name="Sugar")
        pantry = InventoryLocation(name="Pantry")
        db.add_all([item, pantry])
        db.flush()
        db.add(Inventory(item_id=item.id, location_id=pantry.id, quantity=Decimal("3"), unit="each"))
        db.commit()

        first = backfill_from_legacy(db)
        db.commit()
        second = backfill_from_legacy(db)
        db.commit()

        assert second.get("skipped") == 1
        assert first["balances"] == 1
        assert _balances(db)[(item.id, pantry.id)] == Decimal("3")
