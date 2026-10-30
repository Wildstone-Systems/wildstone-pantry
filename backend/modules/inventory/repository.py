"""Inventory event-store + projection persistence (the DB-access layer).

Pure fold logic lives in ``projections``/``domain``; this module is the only
place that touches the database for the event-sourcing path.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.models import Inventory, InventoryLocation, Item
from modules.inventory.domain.lots import Lot
from modules.inventory.models import (
    InventoryBalance,
    InventoryEvent,
    InventoryLot,
    InventoryProjectionState,
)
from modules.inventory.projections import EventData


def append_event(
    db: Session,
    *,
    event_type: str,
    item_id: uuid.UUID,
    location_id: uuid.UUID | None = None,
    quantity: Decimal = Decimal("0"),
    unit: str = "each",
    lot_id: uuid.UUID | None = None,
    target_location_id: uuid.UUID | None = None,
    unit_price: Decimal | None = None,
    expiration_date: date | None = None,
    container_id: uuid.UUID | None = None,
    occurred_at: datetime | None = None,
    source: str = "api",
    actor: str | None = None,
    reason: str | None = None,
    note: str | None = None,
    correlation_id: uuid.UUID | None = None,
    payload: dict | None = None,
) -> InventoryEvent:
    """Append one immutable event. ``seq`` is assigned by the database."""
    event = InventoryEvent(
        event_type=event_type,
        item_id=item_id,
        location_id=location_id,
        quantity=quantity,
        unit=unit,
        lot_id=lot_id,
        target_location_id=target_location_id,
        unit_price=unit_price,
        expiration_date=expiration_date,
        container_id=container_id,
        source=source,
        actor=actor,
        reason=reason,
        note=note,
        correlation_id=correlation_id,
        payload=payload,
    )
    if occurred_at is not None:
        event.occurred_at = occurred_at
    db.add(event)
    db.flush()
    return event


def iter_events(db: Session) -> list[InventoryEvent]:
    """All events in global replay order (``seq``)."""
    return list(db.execute(select(InventoryEvent).order_by(InventoryEvent.seq)).scalars())


def has_events(db: Session) -> bool:
    return db.execute(select(InventoryEvent.seq).limit(1)).first() is not None


def to_event_data(row: InventoryEvent) -> EventData:
    """Map a persisted event onto the DB-free value object the folds consume."""
    return EventData(
        event_type=row.event_type,
        item_id=row.item_id,
        location_id=row.location_id,
        quantity=Decimal(row.quantity),
        unit=row.unit,
        seq=row.seq,
        lot_id=row.lot_id,
        target_location_id=row.target_location_id,
        unit_price=None if row.unit_price is None else Decimal(row.unit_price),
        expiration_date=row.expiration_date,
        container_id=row.container_id,
        **_container_fields(row.payload),
    )


def _container_fields(payload: dict | None) -> dict:
    """Pull the ADR 0014 container metadata out of the event payload (absent on
    legacy events → the fold keeps its pre-0014 behaviour)."""
    payload = payload or {}
    size = payload.get("container_size")
    count = payload.get("sealed_count")
    return {
        "container_size": None if size is None else Decimal(str(size)),
        "container_unit": payload.get("container_unit"),
        "sealed_count": None if count is None else int(count),
    }


def write_projections(
    db: Session,
    *,
    balances: dict[tuple[uuid.UUID, uuid.UUID], Decimal],
    units: dict[tuple[uuid.UUID, uuid.UUID], str],
    lots: Iterable[Lot],
    last_seq: int,
) -> None:
    """Replace the projection tables with a freshly-folded snapshot."""
    db.execute(delete(InventoryBalance))
    db.execute(delete(InventoryLot))

    for (item_id, location_id), quantity in balances.items():
        db.add(
            InventoryBalance(
                item_id=item_id,
                location_id=location_id,
                quantity=quantity,
                unit=units.get((item_id, location_id), "each"),
                last_applied_seq=last_seq,
            )
        )

    for lot in lots:
        db.add(
            InventoryLot(
                id=lot.lot_id,
                item_id=lot.item_id,
                location_id=lot.location_id,
                quantity_remaining=lot.quantity_remaining,
                unit=lot.unit,
                expiration_date=lot.expiration_date,
                unit_price=lot.unit_price,
                status=lot.status,
                frozen=lot.frozen,
                container_id=lot.container_id,
                container_size=lot.container_size,
                container_unit=lot.container_unit,
                sealed_count=lot.sealed_count,
                source_event_id=None,
                last_applied_seq=last_seq,
            )
        )

    _set_projection_state(db, "inv_balance", last_seq)
    _set_projection_state(db, "inv_lot", last_seq)


def _set_projection_state(db: Session, name: str, last_seq: int) -> None:
    state = db.get(InventoryProjectionState, name)
    if state is None:
        db.add(InventoryProjectionState(name=name, last_applied_seq=last_seq))
    else:
        state.last_applied_seq = last_seq


# --- the integration feed (services/feed.py) -----------------------------------


def max_seq(db: Session) -> int:
    return int(db.execute(select(func.max(InventoryEvent.seq))).scalar() or 0)


def has_event_after(db: Session, *, after_seq: int, reason: str, note: str) -> bool:
    return (
        db.execute(
            select(InventoryEvent.seq)
            .where(InventoryEvent.seq > after_seq, InventoryEvent.reason == reason, InventoryEvent.note == note)
            .limit(1)
        ).first()
        is not None
    )


def events_with_item_after(db: Session, *, after_seq: int, limit: int) -> list[tuple[InventoryEvent, str]]:
    """Ledger rows after ``after_seq`` with their item's name, oldest first."""
    rows = db.execute(
        select(InventoryEvent, Item.name)
        .join(Item, InventoryEvent.item_id == Item.id)
        .where(InventoryEvent.seq > after_seq)
        .order_by(InventoryEvent.seq.asc())
        .limit(limit)
    ).all()
    return [(event, name) for event, name in rows]


def events_with_item_between(
    db: Session, *, above_seq: int, through_seq: int, recorded_from: datetime
) -> list[tuple[InventoryEvent, str]]:
    """Ledger rows in (above_seq, through_seq] recorded at or after
    ``recorded_from`` (compared loosely; the caller filters exactly)."""
    rows = db.execute(
        select(InventoryEvent, Item.name)
        .join(Item, InventoryEvent.item_id == Item.id)
        .where(
            InventoryEvent.seq > above_seq,
            InventoryEvent.seq <= through_seq,
            InventoryEvent.recorded_at >= recorded_from,
        )
        .order_by(InventoryEvent.seq.asc())
    ).all()
    return [(event, name) for event, name in rows]


def stock_rows_with_names(db: Session, *, search: str | None = None) -> list[tuple[Inventory, str, str]]:
    """Positive stock rows with item and location names; ``search`` is a
    case-insensitive substring of the item name."""
    query = (
        select(Inventory, Item.name, InventoryLocation.name)
        .join(Item, Inventory.item_id == Item.id)
        .join(InventoryLocation, Inventory.location_id == InventoryLocation.id)
        .where(Inventory.quantity > 0)
    )
    if search:
        query = query.where(func.lower(Item.name).contains(search.strip().lower()))
    return [(row, item, location) for row, item, location in db.execute(query).all()]


def on_hand_by_item(db: Session) -> dict[uuid.UUID, Decimal]:
    """Total on hand per item, across every location."""
    rows = db.execute(
        select(InventoryBalance.item_id, func.sum(InventoryBalance.quantity)).group_by(InventoryBalance.item_id)
    ).all()
    return {item_id: Decimal(total or 0) for item_id, total in rows}


def stock_by_location(db: Session, item_id: uuid.UUID) -> list[tuple[uuid.UUID, Decimal]]:
    """(location_id, quantity) wherever ``item_id`` has positive stock, most first."""
    rows = db.execute(
        select(Inventory.location_id, Inventory.quantity)
        .where(Inventory.item_id == item_id, Inventory.quantity > 0)
        .order_by(Inventory.quantity.desc())
    ).all()
    return [(location_id, Decimal(quantity)) for location_id, quantity in rows]
