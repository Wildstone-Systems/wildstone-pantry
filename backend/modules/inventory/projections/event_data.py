"""DB-free representation of an inventory event + its balance semantics.

``EventData`` is a plain value object the fold functions operate on; the
repository maps ORM ``InventoryEvent`` rows onto it. Keeping the semantics here
(not in the ORM) makes them unit-testable without a database.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

# How each event type contributes to the (item, location) balance, expressed as a
# multiplier on the event's ``quantity``:
#   +1  adds at location_id
#   -1  removes from location_id
#    0  no balance change (status-only events)
# ``transferred`` is special: -quantity at location_id AND +quantity at
# target_location_id (handled in balance_delta).
_BALANCE_SIGN: dict[str, int] = {
    "purchased": +1,
    "produced": +1,      # cooking a recipe yields a new tracked output lot
    "consumed": -1,
    "spoiled": -1,
    "adjusted": +1,      # signed delta (may be negative)
    "reconciled": +1,    # signed true-up (may be negative)
    "opened": 0,
    "frozen": 0,
    "thawed": 0,
    "relabeled": 0,
    "transferred": 0,    # handled explicitly below
}


@dataclass(frozen=True)
class EventData:
    event_type: str
    item_id: uuid.UUID
    location_id: uuid.UUID | None
    quantity: Decimal = Decimal("0")
    unit: str = "each"
    seq: int = 0
    lot_id: uuid.UUID | None = None
    target_location_id: uuid.UUID | None = None
    unit_price: Decimal | None = None
    expiration_date: date | None = None
    container_id: uuid.UUID | None = None
    # Container model (ADR 0014, carried in the event payload so no event-schema
    # change): one physical container's size, and how many sealed containers this
    # intake adds. None on legacy events → the fold keeps its pre-0014 behaviour.
    container_size: Decimal | None = None
    container_unit: str | None = None
    sealed_count: int | None = None
    payload: dict = field(default_factory=dict)


def balance_delta(event: EventData) -> dict[tuple[uuid.UUID, uuid.UUID], Decimal]:
    """Signed quantity change(s) this event makes to (item, location) balances."""
    qty = Decimal(event.quantity or 0)
    deltas: dict[tuple[uuid.UUID, uuid.UUID], Decimal] = {}

    if event.event_type == "transferred":
        if event.location_id is not None:
            deltas[(event.item_id, event.location_id)] = -qty
        if event.target_location_id is not None:
            key = (event.item_id, event.target_location_id)
            deltas[key] = deltas.get(key, Decimal("0")) + qty
        return deltas

    sign = _BALANCE_SIGN.get(event.event_type, 0)
    if sign != 0 and event.location_id is not None:
        deltas[(event.item_id, event.location_id)] = sign * qty
    return deltas
