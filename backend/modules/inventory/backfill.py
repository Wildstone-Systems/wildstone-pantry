"""One-shot backfill of legacy inventory state into the event log (ADR 0002, step 2).

Replays the advisory ``inventory_transactions`` log into events (preserving the
signed balance movements), then appends ``reconciled`` events so the rebuilt
``inv_balance`` matches the authoritative legacy ``inventory.quantity`` exactly.
Additive and idempotent — it does nothing if events already exist. Lots are
best-effort (transactions carry no expiry; current-stock expiry rides in on the
reconcile events).
"""

from __future__ import annotations

import uuid
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Inventory, InventoryTransaction
from modules.inventory import repository
from modules.inventory.projections import replay_balances
from modules.inventory.projector import rebuild_projections

BalanceKey = tuple[uuid.UUID, uuid.UUID]


def _map_transaction(tx: InventoryTransaction) -> dict:
    """Map a legacy transaction onto an event append (signed-delta faithful)."""
    qc = Decimal(tx.quantity_change)
    kind = tx.transaction_type
    common = {"item_id": tx.item_id, "unit": tx.unit, "occurred_at": tx.created_at, "source": "backfill"}
    if kind == "add":
        return {"event_type": "purchased", "location_id": tx.location_id, "quantity": qc, **common}
    if kind == "remove":
        return {"event_type": "consumed", "location_id": tx.location_id, "quantity": -qc, **common}
    if kind == "adjust":
        return {"event_type": "adjusted", "location_id": tx.location_id, "quantity": qc, **common}
    if kind == "move_out":
        # -|qc| at the source location.
        return {"event_type": "transferred", "location_id": tx.location_id, "quantity": -qc, **common}
    if kind == "move_in":
        # +|qc| at the target location.
        return {
            "event_type": "transferred",
            "location_id": None,
            "target_location_id": tx.location_id,
            "quantity": qc,
            **common,
        }
    # Unknown legacy type → record as a signed adjustment so the balance still matches.
    return {"event_type": "adjusted", "location_id": tx.location_id, "quantity": qc, **common}


def backfill_from_legacy(db: Session, *, force: bool = False) -> dict[str, int]:
    """Populate the event log + projections from legacy tables. Idempotent."""
    if repository.has_events(db) and not force:
        return {"skipped": 1, "events": 0, "reconciled": 0}

    transactions = list(
        db.execute(
            select(InventoryTransaction).order_by(InventoryTransaction.created_at, InventoryTransaction.id)
        ).scalars()
    )
    for tx in transactions:
        repository.append_event(db, **_map_transaction(tx))
    db.flush()

    # Replayed balances from the transaction events only (before reconciling).
    replayed = replay_balances([repository.to_event_data(r) for r in repository.iter_events(db)])

    inventory_rows = list(db.execute(select(Inventory)).scalars())
    targets: dict[BalanceKey, Inventory] = {(r.item_id, r.location_id): r for r in inventory_rows}

    reconciled = 0
    # 1) True up every authoritative inventory row.
    for (item_id, location_id), row in targets.items():
        residual = Decimal(row.quantity) - replayed.get((item_id, location_id), Decimal("0"))
        if residual != 0:
            repository.append_event(
                db,
                event_type="reconciled",
                item_id=item_id,
                location_id=location_id,
                quantity=residual,
                unit=row.unit,
                expiration_date=row.expiration_date,
                source="backfill",
                reason="opening reconciliation",
            )
            reconciled += 1
    # 2) Zero out anything the log shows that has no authoritative inventory row.
    for key, qty in replayed.items():
        if key not in targets and qty != Decimal("0"):
            item_id, location_id = key
            repository.append_event(
                db,
                event_type="reconciled",
                item_id=item_id,
                location_id=location_id,
                quantity=-qty,
                source="backfill",
                reason="opening reconciliation (no current stock)",
            )
            reconciled += 1

    db.flush()
    summary = rebuild_projections(db)
    summary["reconciled"] = reconciled
    summary["transactions"] = len(transactions)
    return summary
