"""Container-model fold logic (ADR 0014 Phase 1, step 3).

Pure tests over ``replay_lots``: a lot is a batch of identical containers
(``sealed_count`` × ``container_size``); opening materialises an open container;
consumption draws open-first then auto-opens; discard targets a specific
container. Legacy (pre-0014) events must keep their exact prior behaviour.
"""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

from modules.inventory.domain.lots import replay_lots
from modules.inventory.projections.event_data import EventData

ITEM = uuid.uuid4()
LOC = uuid.uuid4()


def _purchase(seq, qty, *, size=None, unit="each", count=None, lot_id=None, exp=None):
    return EventData(
        event_type="purchased",
        item_id=ITEM,
        location_id=LOC,
        quantity=Decimal(str(qty)),
        unit=unit,
        seq=seq,
        lot_id=lot_id,
        expiration_date=exp,
        container_size=None if size is None else Decimal(str(size)),
        container_unit=unit if size is not None else None,
        sealed_count=count,
    )


def _consume(seq, qty, unit="each"):
    return EventData(event_type="consumed", item_id=ITEM, location_id=LOC, quantity=Decimal(str(qty)), unit=unit, seq=seq)


def _discard(seq, qty, lot_id, unit="each"):
    return EventData(
        event_type="spoiled", item_id=ITEM, location_id=LOC, quantity=Decimal(str(qty)), unit=unit, seq=seq, lot_id=lot_id
    )


def _total(lots):
    return sum((lot.quantity_remaining for lot in lots.values()), Decimal("0"))


# ---- Milk scenario ----------------------------------------------------------


def test_milk_open_consume_then_discard_open_container() -> None:
    # 2 sealed gallons (one lot, two containers).
    events = [_purchase(1, 2, size=1, unit="gal", count=2)]
    # Use half a gallon → auto-opens one container, drawing 0.5 from it.
    events.append(_consume(2, "0.5", unit="gal"))
    lots = replay_lots(events)

    batch = next(lot for lot in lots.values() if lot.status != "open")
    open_ctr = next(lot for lot in lots.values() if lot.status == "open")
    assert batch.sealed_count == 1 and batch.quantity_remaining == Decimal("1")  # one sealed gallon left
    assert open_ctr.quantity_remaining == Decimal("0.5")  # partial open jug
    assert _total(lots) == Decimal("1.5")

    # The open jug goes bad — discard that specific container.
    events.append(_discard(3, "0.5", open_ctr.lot_id, unit="gal"))
    lots = replay_lots(events)

    batch = next(lot for lot in lots.values() if lot.status != "open" and lot.quantity_remaining > 0)
    assert batch.sealed_count == 1 and batch.quantity_remaining == Decimal("1")  # untouched sealed gallon
    assert _total(lots) == Decimal("1")


# ---- Yogurt scenario (lot != container) -------------------------------------


def test_yogurt_three_lots_discard_one_sealed_tub() -> None:
    a, b, c = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    events = [
        _purchase(1, 1, size=1, unit="qt", count=1, lot_id=a, exp=date(2026, 8, 8)),
        _purchase(2, 1, size=1, unit="qt", count=1, lot_id=b, exp=date(2026, 8, 11)),
        _purchase(3, 4, size=1, unit="qt", count=4, lot_id=c, exp=date(2026, 8, 15)),
    ]
    # Discard one sealed tub of the four-tub lot.
    events.append(_discard(4, 1, c, unit="qt"))
    lots = replay_lots(events)

    assert lots[c].sealed_count == 3 and lots[c].quantity_remaining == Decimal("3")
    assert lots[a].sealed_count == 1 and lots[a].quantity_remaining == Decimal("1")  # untouched
    assert lots[b].sealed_count == 1 and lots[b].quantity_remaining == Decimal("1")  # untouched
    assert _total(lots) == Decimal("5")


def test_yogurt_fefo_consume_opens_soonest_expiry_first() -> None:
    a, b, c = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    events = [
        _purchase(1, 1, size=1, unit="qt", count=1, lot_id=a, exp=date(2026, 8, 8)),
        _purchase(2, 1, size=1, unit="qt", count=1, lot_id=b, exp=date(2026, 8, 11)),
        _purchase(3, 4, size=1, unit="qt", count=4, lot_id=c, exp=date(2026, 8, 15)),
        _consume(4, "1.5", unit="qt"),  # eat 1.5 qt across the two soonest
    ]
    lots = replay_lots(events)

    assert lots[a].quantity_remaining == Decimal("0") and lots[a].status == "depleted"  # earliest fully used
    assert lots[c].sealed_count == 4 and lots[c].quantity_remaining == Decimal("4")  # latest untouched
    assert _total(lots) == Decimal("4.5")  # 6 on hand − 1.5 consumed


# ---- Legacy parity ----------------------------------------------------------


def test_legacy_events_without_containers_are_unchanged() -> None:
    # No container_size → the pre-0014 plain-FEFO quantity behaviour.
    events = [_purchase(1, 5, lot_id=uuid.uuid4()), _consume(2, 2)]
    lots = replay_lots(events)
    (lot,) = list(lots.values())
    assert lot.quantity_remaining == Decimal("3")
    assert lot.container_size is None and lot.sealed_count is None
