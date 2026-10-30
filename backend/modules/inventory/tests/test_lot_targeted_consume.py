"""Lot-targeted consumption — ADR 0023 D1/D2.

The yogurt case: a household keeps several quart tubs going at once, partially
consumes them, and eventually throws an empty one away. Before this, a consume
was always FEFO-allocated, so "I finished THIS tub" could not be expressed —
scanning out an empty container removed a whole container's worth of content,
draining the open tub and then cracking a sealed one to make up the difference.

These are pure fold tests over ``replay_lots``. The parity tests matter as much
as the new behaviour: every historical ``consumed`` event has no ``lot_id``, so
the untargeted path must stay bit-for-bit what it was.
"""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

from modules.inventory.domain.lots import replay_lots
from modules.inventory.projections.event_data import EventData

ITEM = uuid.uuid4()
LOC = uuid.uuid4()


def _purchase(seq, qty, *, size=None, unit="qt", count=None, lot_id=None, exp=None):
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


def _consume(seq, qty, *, lot_id=None, unit="qt"):
    return EventData(
        event_type="consumed",
        item_id=ITEM,
        location_id=LOC,
        quantity=Decimal(str(qty)),
        unit=unit,
        seq=seq,
        lot_id=lot_id,
    )


def _restore(seq, qty, lot_id, unit="qt"):
    """The undo of a targeted removal: a positive targeted ``adjusted``."""
    return EventData(
        event_type="adjusted",
        item_id=ITEM,
        location_id=LOC,
        quantity=Decimal(str(qty)),
        unit=unit,
        seq=seq,
        lot_id=lot_id,
    )


def _total(lots):
    return sum((lot.quantity_remaining for lot in lots.values()), Decimal("0"))


def _open_lots(lots):
    return [lot for lot in lots.values() if lot.status == "open" and lot.quantity_remaining > 0]


# ---- The yogurt case --------------------------------------------------------


def test_finishing_the_open_tub_takes_only_the_remainder() -> None:
    """Three quarts, half of one eaten, then the empty tub is thrown out.

    The finish must consume 0.5 qt — what was actually left — not a full quart.
    Taking a container's worth would drain the open tub AND open a sealed one to
    cover the shortfall, losing half a quart off the books and inventing a
    half-empty container that does not exist.
    """
    events = [_purchase(1, 3, size=1, count=3)]
    events.append(_consume(2, "0.5"))  # untargeted partial use → auto-opens a tub
    lots = replay_lots(events)

    open_ctr = next(lot for lot in _open_lots(lots))
    assert open_ctr.quantity_remaining == Decimal("0.5")
    assert _total(lots) == Decimal("2.5")

    # "I finished this tub" — targeted at the open container.
    events.append(_consume(3, "0.5", lot_id=open_ctr.lot_id))
    lots = replay_lots(events)

    assert _total(lots) == Decimal("2")  # exactly two untouched quarts remain
    assert _open_lots(lots) == []  # and no phantom open container
    batch = next(lot for lot in lots.values() if lot.quantity_remaining > 0)
    assert batch.sealed_count == 2


def test_finish_targets_the_named_tub_not_the_soonest_to_expire() -> None:
    """Two tubs open at once — the whole reason the prompt exists.

    FEFO would take from the earlier-expiring tub. When the human says they
    emptied the *other* one, the fold must believe them.
    """
    early, late = uuid.uuid4(), uuid.uuid4()
    events = [
        _purchase(1, 1, size=1, count=1, lot_id=early, exp=date(2026, 1, 1)),
        _purchase(2, 1, size=1, count=1, lot_id=late, exp=date(2026, 6, 1)),
        _consume(3, "0.5"),  # opens the early-expiring tub (FEFO)
        _consume(4, "0.75"),  # drains it, then auto-opens the later one
    ]
    lots = replay_lots(events)
    opens = _open_lots(lots)
    assert len(opens) == 1  # the later tub, now open with 0.75 left
    later_open = opens[0]
    assert later_open.expiration_date == date(2026, 6, 1)
    assert later_open.quantity_remaining == Decimal("0.75")

    # Re-stock an early-expiring tub so two are live, one open and one sealed.
    events.append(_purchase(5, 1, size=1, count=1, lot_id=uuid.uuid4(), exp=date(2026, 2, 1)))
    # Finish the OPEN later-expiring tub by name. FEFO would have hit the new
    # early-expiring sealed one instead.
    events.append(_consume(6, "0.75", lot_id=later_open.lot_id))
    lots = replay_lots(events)

    assert _total(lots) == Decimal("1")
    survivor = next(lot for lot in lots.values() if lot.quantity_remaining > 0)
    assert survivor.expiration_date == date(2026, 2, 1)  # the untouched sealed tub


def test_partial_draw_from_a_named_tub() -> None:
    """"Used some of this one" — a targeted consume smaller than the remainder."""
    events = [_purchase(1, 2, size=1, count=2), _consume(2, "0.25")]
    lots = replay_lots(events)
    open_ctr = _open_lots(lots)[0]
    assert open_ctr.quantity_remaining == Decimal("0.75")

    events.append(_consume(3, "0.25", lot_id=open_ctr.lot_id))
    lots = replay_lots(events)

    still_open = _open_lots(lots)[0]
    assert still_open.lot_id == open_ctr.lot_id  # same tub, not a new one
    assert still_open.quantity_remaining == Decimal("0.5")
    assert _total(lots) == Decimal("1.5")


def test_finishing_a_sealed_batch_takes_one_container_only() -> None:
    """A whole tub eaten in one sitting, nothing open beforehand: the targeted
    consume of a sealed batch must take ONE container, never the whole batch."""
    events = [_purchase(1, 3, size=1, count=3)]
    batch = replay_lots(events)
    batch_id = next(iter(batch))

    events.append(_consume(2, 1, lot_id=batch_id))
    lots = replay_lots(events)

    remaining = lots[batch_id]
    assert remaining.quantity_remaining == Decimal("2")
    assert remaining.sealed_count == 2


# ---- Undo -------------------------------------------------------------------


def test_undoing_a_finish_restores_the_same_container() -> None:
    """The undo has to put the content back into the tub it came out of.

    Re-adding as a fresh lot would lose the expiry and leave the original
    depleted — the container identity this whole design protects, destroyed by
    its own undo.
    """
    events = [_purchase(1, 2, size=1, count=2, exp=date(2026, 3, 1)), _consume(2, "0.5")]
    lots = replay_lots(events)
    open_ctr = _open_lots(lots)[0]

    events.append(_consume(3, "0.5", lot_id=open_ctr.lot_id))
    assert _open_lots(replay_lots(events)) == []

    events.append(_restore(4, "0.5", open_ctr.lot_id))
    lots = replay_lots(events)

    revived = lots[open_ctr.lot_id]
    assert revived.quantity_remaining == Decimal("0.5")
    assert revived.status == "open"  # revived, not left dead
    assert revived.expiration_date == date(2026, 3, 1)  # identity intact
    assert _total(lots) == Decimal("1.5")


# ---- Parity with pre-0023 history -------------------------------------------


def test_untargeted_consume_is_unchanged() -> None:
    """No historical ``consumed`` event carries a lot_id, so the container path
    must fold exactly as before."""
    events = [_purchase(1, 2, size=1, count=2), _consume(2, "0.5"), _consume(3, "0.75")]
    lots = replay_lots(events)

    assert _total(lots) == Decimal("0.75")
    opens = _open_lots(lots)
    assert len(opens) == 1 and opens[0].quantity_remaining == Decimal("0.75")


def test_untargeted_consume_on_legacy_lots_is_unchanged() -> None:
    """Container-less (pre-0014) stock keeps the plain FEFO reduce."""
    events = [_purchase(1, 5, unit="each"), _consume(2, 2, unit="each")]
    lots = replay_lots(events)
    assert _total(lots) == Decimal("3")


def test_consume_naming_an_unknown_lot_falls_back_to_fefo() -> None:
    """A lot_id that isn't in the fold (pruned, or from another location) must
    not silently drop the consumption on the floor."""
    events = [_purchase(1, 2, size=1, count=2), _consume(2, 1, lot_id=uuid.uuid4())]
    lots = replay_lots(events)
    assert _total(lots) == Decimal("1")
