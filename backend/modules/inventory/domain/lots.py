"""Lot model + FEFO allocation (pure) — ADR 0002.

A lot is a distinct intake batch with its own expiry/price. Consumption,
spoilage and outbound transfers allocate against lots **first-expiry-first-out**
(FEFO), tie-broken by intake order (``seq``). Lots are derived from the event
log: ``replay_lots`` rebuilds them deterministically, lot identity always coming
from the creating event's ``lot_id``.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from modules.inventory.projections.event_data import EventData

# Lots with no expiration are consumed last (dated stock leaves first).
_FAR_FUTURE = date.max


@dataclass
class Lot:
    lot_id: uuid.UUID
    item_id: uuid.UUID
    location_id: uuid.UUID
    quantity_remaining: Decimal
    unit: str = "each"
    expiration_date: date | None = None
    unit_price: Decimal | None = None
    status: str = "sealed"  # sealed | open | depleted
    frozen: bool = False
    container_id: uuid.UUID | None = None
    # Container model (ADR 0014). A SEALED-BATCH lot holds ``sealed_count``
    # identical containers of ``container_size``; an OPEN container is its own lot
    # (status="open", sealed_count=0) whose ``quantity_remaining`` is the partial
    # content left. ``container_size`` is None on pre-0014 (legacy) lots.
    container_size: Decimal | None = None
    container_unit: str | None = None
    sealed_count: int | None = None
    seq: int = 0


@dataclass(frozen=True)
class Allocation:
    lot_id: uuid.UUID
    quantity: Decimal


def _fefo_key(lot: Lot) -> tuple[date, int]:
    return (lot.expiration_date or _FAR_FUTURE, lot.seq)


def allocate_fefo(lots: Iterable[Lot], quantity: Decimal) -> tuple[list[Allocation], Decimal]:
    """Allocate ``quantity`` across ``lots`` FEFO.

    Returns the allocations and any unmet ``shortfall`` (when lots don't cover
    the requested quantity). Does not mutate the lots.
    """
    need = Decimal(quantity)
    allocations: list[Allocation] = []
    for lot in sorted(lots, key=_fefo_key):
        if need <= 0:
            break
        available = Decimal(lot.quantity_remaining)
        if available <= 0:
            continue
        take = min(available, need)
        allocations.append(Allocation(lot_id=lot.lot_id, quantity=take))
        need -= take
    return allocations, need


def _lots_at(lots: dict[uuid.UUID, Lot], item_id: uuid.UUID, location_id: uuid.UUID | None) -> list[Lot]:
    return [
        lot
        for lot in lots.values()
        if lot.item_id == item_id and lot.location_id == location_id and lot.quantity_remaining > 0
    ]


def _reduce(lots: dict[uuid.UUID, Lot], item_id: uuid.UUID, location_id: uuid.UUID | None, qty: Decimal) -> None:
    for alloc in allocate_fefo(_lots_at(lots, item_id, location_id), qty)[0]:
        lot = lots[alloc.lot_id]
        lot.quantity_remaining -= alloc.quantity
        if lot.quantity_remaining <= 0:
            lot.status = "depleted"


def _create_lot(lots: dict[uuid.UUID, Lot], event: EventData) -> None:
    lot_id = event.lot_id or uuid.uuid5(uuid.NAMESPACE_OID, f"lot:{event.seq}")
    if event.location_id is None:
        return
    existing = lots.get(lot_id)
    if existing is not None:
        existing.quantity_remaining += Decimal(event.quantity)
        # Undoing a finish/discard (a positive targeted `adjusted`) has to put
        # back everything the removal took, not just the quantity. A removal that
        # dropped whole sealed containers decremented `sealed_count`, so the
        # compensating event carries that count and we re-add it here — restoring
        # quantity alone would leave a batch claiming fewer physical containers
        # than it holds, which then misleads the worklist and scan planning.
        if event.sealed_count:
            existing.sealed_count = (existing.sealed_count or 0) + int(event.sealed_count)
        # Revive it too, or the restored content sits on a lot the projection
        # drops as dead. Sealed units back on the shelf means a sealed batch;
        # none means this was the partially-used open container.
        if existing.status == "depleted" and existing.quantity_remaining > 0:
            existing.status = "open" if (existing.sealed_count or 0) <= 0 and existing.container_size else "sealed"
        return
    lots[lot_id] = Lot(
        lot_id=lot_id,
        item_id=event.item_id,
        location_id=event.location_id,
        quantity_remaining=Decimal(event.quantity),
        unit=event.unit,
        expiration_date=event.expiration_date,
        unit_price=event.unit_price,
        status="sealed",
        container_id=event.container_id,
        container_size=event.container_size,
        container_unit=event.container_unit,
        sealed_count=event.sealed_count,
        seq=event.seq,
    )


# ---------------------------------------------------------------------------
# Container model (ADR 0014) — only engaged for lots that carry a container_size;
# legacy lots keep the plain FEFO quantity behaviour above.
# ---------------------------------------------------------------------------


def _has_container_lots(lots: dict[uuid.UUID, Lot], item_id: uuid.UUID, location_id: uuid.UUID | None) -> bool:
    return any(
        lot.container_size is not None and lot.item_id == item_id and lot.location_id == location_id
        for lot in lots.values()
    )


def _open_one_container(lots: dict[uuid.UUID, Lot], batch: Lot, seq: int) -> Lot | None:
    """Take one sealed container out of ``batch`` and materialise it as an open
    lot with its full container_size as remaining content."""
    size = batch.container_size
    if size is None or (batch.sealed_count or 0) <= 0:
        return None
    batch.sealed_count -= 1
    batch.quantity_remaining -= size
    if batch.quantity_remaining <= 0 and (batch.sealed_count or 0) <= 0:
        batch.status = "depleted"
    open_id = uuid.uuid5(uuid.NAMESPACE_OID, f"open:{seq}:{batch.lot_id}:{batch.sealed_count}")
    lots[open_id] = Lot(
        lot_id=open_id,
        item_id=batch.item_id,
        location_id=batch.location_id,
        quantity_remaining=size,
        unit=batch.unit,
        expiration_date=batch.expiration_date,
        unit_price=batch.unit_price,
        status="open",
        container_size=size,
        container_unit=batch.container_unit,
        sealed_count=0,
        seq=seq,
    )
    return lots[open_id]


def _consume_containers(
    lots: dict[uuid.UUID, Lot], item_id: uuid.UUID, location_id: uuid.UUID | None, qty: Decimal, seq: int
) -> Decimal:
    """Draw ``qty`` from open containers first (FEFO), auto-opening the next
    sealed container when the open ones run dry. Returns any shortfall left for
    the caller to satisfy against legacy (non-container) lots."""

    def _open() -> list[Lot]:
        return sorted(
            (
                lot
                for lot in lots.values()
                if lot.item_id == item_id
                and lot.location_id == location_id
                and lot.status == "open"
                and lot.quantity_remaining > 0
            ),
            key=_fefo_key,
        )

    def _sealed() -> list[Lot]:
        return sorted(
            (
                lot
                for lot in lots.values()
                if lot.item_id == item_id
                and lot.location_id == location_id
                and lot.status != "open"
                and lot.container_size is not None
                and (lot.sealed_count or 0) > 0
            ),
            key=_fefo_key,
        )

    def _loose() -> list[Lot]:
        """Lots at this key holding no containers at all — a cook's yield that
        stayed in the serving dish while the rest went into tubs (ADR 0025 §4),
        or pre-0014 stock. Drawn before a sealed container is cracked, for the
        same reason open containers are: finish what's already out."""
        return sorted(
            (
                lot
                for lot in lots.values()
                if lot.item_id == item_id
                and lot.location_id == location_id
                and lot.status != "open"
                and lot.container_size is None
                and lot.quantity_remaining > 0
            ),
            key=_fefo_key,
        )

    need = Decimal(qty)
    while need > 0:
        candidates = _open() or _loose()
        if candidates:
            lot = candidates[0]
            take = min(lot.quantity_remaining, need)
            lot.quantity_remaining -= take
            need -= take
            if lot.quantity_remaining <= 0:
                lot.status = "depleted"
            continue
        sealed = _sealed()
        if not sealed:
            break  # no more container stock — caller falls back to legacy lots
        _open_one_container(lots, sealed[0], seq)
    return need


def _reduce_targeted_lot(lot: Lot, qty: Decimal) -> None:
    """Remove ``qty`` from a specific targeted lot — a discard/spoil of a chosen
    container, a targeted correction, or a targeted CONSUME ("I finished this
    tub", ADR 0023) — decrementing the sealed count for whole-container drops.

    Targeting is what makes the yogurt case correct: with two tubs open, the one
    that was emptied is a fact only the human knows, so the event names it and
    this reduces THAT lot instead of guessing FEFO.
    """
    lot.quantity_remaining -= Decimal(qty)
    if lot.status != "open" and lot.container_size:
        dropped = int((Decimal(qty) / lot.container_size).to_integral_value(rounding=ROUND_HALF_UP))
        lot.sealed_count = max(0, (lot.sealed_count or 0) - dropped)
    if lot.quantity_remaining <= 0 and (lot.sealed_count or 0) <= 0:
        lot.status = "depleted"


def replay_lots(events: Iterable[EventData]) -> dict[uuid.UUID, Lot]:
    """Rebuild lots from the event stream (events must be in ``seq`` order)."""
    lots: dict[uuid.UUID, Lot] = {}
    for event in events:
        kind = event.event_type
        if kind == "purchased" or kind == "produced":
            # A produced fact (recipe yield) becomes its own lot, exactly like an
            # intake — carrying the output's optional expiry / price / weight.
            _create_lot(lots, event)
        elif kind == "consumed":
            # A consume naming a lot draws from THAT container (ADR 0023 D1) —
            # "I finished this tub", where which tub is a fact only the human
            # has. No historical `consumed` event carries a lot_id, so the
            # fallback below is bit-for-bit the pre-0023 behaviour.
            if event.lot_id is not None and event.lot_id in lots:
                _reduce_targeted_lot(lots[event.lot_id], Decimal(event.quantity))
            # Container-aware when this (item, location) has container lots;
            # otherwise the plain FEFO reduce (pre-0014 behaviour, exact parity).
            elif _has_container_lots(lots, event.item_id, event.location_id):
                shortfall = _consume_containers(
                    lots, event.item_id, event.location_id, Decimal(event.quantity), event.seq
                )
                if shortfall > 0:
                    _reduce(lots, event.item_id, event.location_id, shortfall)
            else:
                _reduce(lots, event.item_id, event.location_id, Decimal(event.quantity))
        elif kind == "spoiled":
            # A discard targeting a specific container removes from THAT lot; a
            # bare spoil (no target) falls back to FEFO (pre-0014 behaviour).
            if event.lot_id is not None and event.lot_id in lots:
                _reduce_targeted_lot(lots[event.lot_id], Decimal(event.quantity))
            else:
                _reduce(lots, event.item_id, event.location_id, Decimal(event.quantity))
        elif kind == "transferred":
            _reduce(lots, event.item_id, event.location_id, Decimal(event.quantity))
            if event.target_location_id is not None:
                _create_lot(
                    lots,
                    EventData(
                        event_type="purchased",
                        item_id=event.item_id,
                        location_id=event.target_location_id,
                        quantity=event.quantity,
                        unit=event.unit,
                        seq=event.seq,
                        lot_id=event.lot_id,
                        unit_price=event.unit_price,
                        expiration_date=event.expiration_date,
                    ),
                )
        elif kind == "adjusted" or kind == "reconciled":
            qty = Decimal(event.quantity)
            if qty >= 0:
                _create_lot(lots, event)
            elif event.lot_id is not None and event.lot_id in lots:
                # A targeted correction (e.g. "remove this lot, added by mistake")
                # reduces THAT lot — not FEFO — and isn't consumption or waste.
                _reduce_targeted_lot(lots[event.lot_id], -qty)
            else:
                _reduce(lots, event.item_id, event.location_id, -qty)
        elif kind == "opened" and event.lot_id in lots:
            # Materialise one open container from the sealed batch (ADR 0014). A
            # legacy lot with no container_size just flips to open, as before.
            batch = lots[event.lot_id]
            if batch.container_size is not None:
                _open_one_container(lots, batch, event.seq)
            else:
                batch.status = "open"
        elif kind in ("frozen", "thawed", "relabeled") and event.lot_id in lots:
            lot = lots[event.lot_id]
            if kind == "frozen":
                lot.frozen = True
            elif kind == "thawed":
                lot.frozen = False
            elif kind == "relabeled" and event.expiration_date is not None:
                lot.expiration_date = event.expiration_date
    return lots
