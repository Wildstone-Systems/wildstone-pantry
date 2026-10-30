"""Read-only ledger feed for integrations (ADR 0026 §4).

The inventory ledger (ADR 0002) is append-only and sequenced, which makes it a
ready-made outbox: an integration keeps a cursor (``seq``) and reads forward.
This is the published interface for doing that — other modules never query
``inventory_events`` directly.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy.orm import Session

from modules.inventory import repository
from modules.inventory.models import InventoryEvent


def _as_utc(value: datetime) -> datetime:
    # SQLite hands timestamps back naive; they were written as UTC.
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def _row(event: InventoryEvent, item_name: str) -> dict:
    return {
        "seq": int(event.seq),
        "event_id": event.event_id,
        "event_type": event.event_type,
        "item_id": event.item_id,
        "item_name": item_name,
        "location_id": event.location_id,
        "quantity": Decimal(event.quantity),
        "unit": event.unit,
        "occurred_at": _as_utc(event.occurred_at),
        "recorded_at": _as_utc(event.recorded_at),
        "actor": event.actor,
        "source": event.source,
        "reason": event.reason,
        "note": event.note,
        "correlation_id": event.correlation_id,
        "payload": event.payload or {},
    }


def head_seq(db: Session) -> int:
    """The newest ledger sequence number (0 for an empty ledger)."""
    return repository.max_seq(db)


def recipe_deductions_after(db: Session, *, recipe_id, after_seq: int) -> bool:
    """Has stock been taken out for this recipe since ``after_seq``?

    How a cook that failed part-way tells "nothing happened, try again" from
    "the pantry was already changed" (deductions commit one by one).
    """
    return repository.has_event_after(db, after_seq=after_seq, reason="recipe_cook", note=f"recipe:{recipe_id}")


def events_after(
    db: Session, *, after_seq: int, limit: int = 500, settled_before: datetime | None = None
) -> list[dict]:
    """Ledger rows after ``after_seq``, oldest first, with item names resolved.

    ``settled_before`` stops the page at the first row recorded after that
    moment. Sequence numbers are handed out before commit, so a row with a lower
    number can become visible after a higher one; leaving the newest few seconds
    for the next read keeps a cursor from stepping over it.
    """
    rows = repository.events_with_item_after(db, after_seq=after_seq, limit=limit)

    out: list[dict] = []
    for event, item_name in rows:
        if settled_before is not None and _as_utc(event.recorded_at) > settled_before:
            break
        out.append(_row(event, item_name))
    return out


def late_events(
    db: Session, *, through_seq: int, recorded_since: datetime, floor_seq: int = 0, window: int = 2000
) -> list[dict]:
    """Recent rows at or below ``through_seq`` — for a reader whose cursor may
    have stepped over a row that became visible late.

    A sequence number is handed out when a row is written, not when it commits,
    so a slow transaction's row can appear BELOW a cursor that has already
    moved past it. Re-reading the recently recorded rows below the cursor finds
    it; the reader drops the ones it already handled (its event ids are
    stable). Nothing at or below ``floor_seq`` is re-read, and ``window``
    bounds the re-read by sequence so it never scans the whole ledger.
    """
    rows = repository.events_with_item_between(
        db,
        above_seq=max(floor_seq, through_seq - window),
        through_seq=through_seq,
        # Loose in SQL (SQLite compares naive text); exact below.
        recorded_from=recorded_since - timedelta(minutes=1),
    )
    return [_row(e, name) for e, name in rows if _as_utc(e.recorded_at) >= recorded_since]


def on_hand_by_item(db: Session) -> dict:
    """Total on hand per item id, across every location — the figure restock
    thresholds are measured against."""
    return repository.on_hand_by_item(db)


def fullest_location(db: Session, item_id) -> object | None:
    """Where most of ``item_id`` is kept, or None when there is none in stock —
    where an agent's "I had two eggs" takes them from."""
    rows = repository.stock_by_location(db, item_id)
    return rows[0][0] if rows else None


def household_stock(db: Session, *, search: str | None = None) -> list[dict]:
    """What is on hand across every location, one row per item.

    ``search`` is a case-insensitive substring match on the item name.
    """
    by_item: dict = {}
    for row, item_name, location_name in repository.stock_rows_with_names(db, search=search):
        entry = by_item.setdefault(
            row.item_id,
            {"item_id": row.item_id, "item": item_name, "quantity": Decimal("0"), "unit": row.unit, "locations": []},
        )
        entry["quantity"] += Decimal(row.quantity)
        if location_name not in entry["locations"]:
            entry["locations"].append(location_name)
    return sorted(by_item.values(), key=lambda e: e["item"].lower())
