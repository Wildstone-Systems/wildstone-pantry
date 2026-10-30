"""Pure projection / FEFO tests (no DB) — ADR 0002 step 1."""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

from modules.inventory.domain.lots import Lot, allocate_fefo, replay_lots
from modules.inventory.projections import EventData, replay_balances

ITEM = uuid.uuid4()
LOC_A = uuid.uuid4()
LOC_B = uuid.uuid4()


def _ev(seq: int, event_type: str, **kw) -> EventData:
    return EventData(event_type=event_type, item_id=ITEM, location_id=kw.pop("location_id", LOC_A), seq=seq, **kw)


# ---------------------------------------------------------------------------
# Balance fold
# ---------------------------------------------------------------------------

def test_balance_sums_signed_contributions():
    events = [
        _ev(1, "purchased", quantity=Decimal("10")),
        _ev(2, "consumed", quantity=Decimal("3")),
        _ev(3, "spoiled", quantity=Decimal("1")),
        _ev(4, "adjusted", quantity=Decimal("-2")),
        _ev(5, "reconciled", quantity=Decimal("0.5")),
        _ev(6, "opened", quantity=Decimal("0")),  # status-only: no balance change
    ]
    balances = replay_balances(events)
    assert balances[(ITEM, LOC_A)] == Decimal("4.5")


def test_transfer_moves_quantity_between_locations():
    events = [
        _ev(1, "purchased", quantity=Decimal("5")),
        _ev(2, "transferred", quantity=Decimal("2"), target_location_id=LOC_B),
    ]
    balances = replay_balances(events)
    assert balances[(ITEM, LOC_A)] == Decimal("3")
    assert balances[(ITEM, LOC_B)] == Decimal("2")


def test_status_only_events_do_not_change_balance():
    events = [
        _ev(1, "purchased", quantity=Decimal("5")),
        _ev(2, "frozen", quantity=Decimal("0")),
        _ev(3, "thawed", quantity=Decimal("0")),
        _ev(4, "relabeled", quantity=Decimal("0")),
    ]
    assert replay_balances(events)[(ITEM, LOC_A)] == Decimal("5")


# ---------------------------------------------------------------------------
# FEFO allocation
# ---------------------------------------------------------------------------

def test_allocate_fefo_consumes_earliest_expiry_first():
    later = Lot(uuid.uuid4(), ITEM, LOC_A, Decimal("5"), expiration_date=date(2026, 12, 1), seq=1)
    sooner = Lot(uuid.uuid4(), ITEM, LOC_A, Decimal("5"), expiration_date=date(2026, 6, 1), seq=2)
    allocations, shortfall = allocate_fefo([later, sooner], Decimal("6"))
    assert shortfall == 0
    # sooner (earlier expiry) first, fully, then 1 from later
    assert allocations[0].lot_id == sooner.lot_id and allocations[0].quantity == Decimal("5")
    assert allocations[1].lot_id == later.lot_id and allocations[1].quantity == Decimal("1")


def test_allocate_fefo_reports_shortfall():
    lot = Lot(uuid.uuid4(), ITEM, LOC_A, Decimal("2"), seq=1)
    allocations, shortfall = allocate_fefo([lot], Decimal("5"))
    assert sum(a.quantity for a in allocations) == Decimal("2")
    assert shortfall == Decimal("3")


def test_undated_lots_consumed_after_dated():
    undated = Lot(uuid.uuid4(), ITEM, LOC_A, Decimal("5"), expiration_date=None, seq=1)
    dated = Lot(uuid.uuid4(), ITEM, LOC_A, Decimal("5"), expiration_date=date(2026, 7, 1), seq=2)
    allocations, _ = allocate_fefo([undated, dated], Decimal("1"))
    assert allocations[0].lot_id == dated.lot_id


# ---------------------------------------------------------------------------
# Lot replay
# ---------------------------------------------------------------------------

def test_replay_lots_creates_and_depletes_across_lots():
    lot1, lot2 = uuid.uuid4(), uuid.uuid4()
    events = [
        _ev(1, "purchased", quantity=Decimal("4"), lot_id=lot1, expiration_date=date(2026, 6, 1)),
        _ev(2, "purchased", quantity=Decimal("4"), lot_id=lot2, expiration_date=date(2026, 9, 1)),
        _ev(3, "consumed", quantity=Decimal("5")),  # 4 from lot1 (sooner), 1 from lot2
    ]
    lots = replay_lots(events)
    assert lots[lot1].quantity_remaining == Decimal("0")
    assert lots[lot1].status == "depleted"
    assert lots[lot2].quantity_remaining == Decimal("3")


def test_replay_lots_status_transitions():
    lot1 = uuid.uuid4()
    events = [
        _ev(1, "purchased", quantity=Decimal("2"), lot_id=lot1),
        _ev(2, "frozen", quantity=Decimal("0"), lot_id=lot1),
        _ev(3, "opened", quantity=Decimal("0"), lot_id=lot1),
    ]
    lots = replay_lots(events)
    assert lots[lot1].frozen is True
    assert lots[lot1].status == "open"
