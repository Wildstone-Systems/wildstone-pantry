"""The tracking worklist — ADR 0023 D8.

The rows someone should plausibly act on right now, in one ranked list. This is
the surface's primary content, not a sidebar: tapping a row needs no barcode, no
typing and no knowing what the item is called in the catalog, which makes it the
only interaction here a distracted family member reliably completes.

Three feeds, deliberately:

* **expiring** — the triage case, and the one with a deadline.
* **open** — containers already started. The yogurt case: what is half-used is
  what someone is most likely to finish next.
* **recent** — items this household actually consumes often. The stated problem
  is ordinary usage going unrecorded, not only spoilage, so a feed built purely
  from expiry dates would miss most of it.

Rows are deduplicated by lot (and by item for the lot-less ``recent`` feed) so
the same container never appears twice under different justifications.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Item
from modules.inventory.models import InventoryBalance, InventoryEvent, InventoryLot

# How far back "recently consumed" looks. Long enough to catch a weekly staple,
# short enough that the list reflects how the household eats now.
RECENT_WINDOW_DAYS = 21


def _row(
    *,
    source: str,
    item: Item,
    location_id: UUID,
    lot: InventoryLot | None = None,
    quantity: Decimal | None = None,
    unit: str | None = None,
) -> dict:
    expiry = lot.expiration_date if lot is not None else None
    return {
        "source": source,
        "item_id": item.id,
        "item_name": item.name,
        "location_id": location_id,
        "lot_id": lot.id if lot is not None else None,
        "quantity_remaining": quantity if quantity is not None else (lot.quantity_remaining if lot else Decimal("0")),
        "unit": unit or (lot.unit if lot is not None else None),
        "expiration_date": expiry,
        "days_until_expiration": (expiry - date.today()).days if expiry else None,
        "status": lot.status if lot is not None else None,
        "sealed_count": lot.sealed_count if lot is not None else None,
        "container_size": lot.container_size if lot is not None else None,
    }


def _live_lots(db: Session, location_id: UUID) -> list[tuple[InventoryLot, Item]]:
    return list(
        db.execute(
            select(InventoryLot, Item)
            .join(Item, InventoryLot.item_id == Item.id)
            .where(InventoryLot.location_id == location_id, InventoryLot.quantity_remaining > 0)
        ).all()
    )


def build_worklist(db: Session, *, location_id: UUID, within_days: int = 7, limit: int = 24) -> list[dict]:
    """Rank what's worth acting on at ``location_id``.

    Ordering is by urgency, not by feed: anything already expired comes first,
    then stock expiring inside ``within_days`` soonest-first, then open
    containers with no deadline, then recently-consumed items. A row that would
    qualify twice (an open container that is also expiring) appears once, under
    the more urgent reason.
    """
    lots = _live_lots(db, location_id)
    cutoff = date.today() + timedelta(days=within_days)

    rows: list[dict] = []
    claimed_lots: set[UUID] = set()
    claimed_items: set[UUID] = set()

    # 1. Expiring — skipping the two things the household has deliberately
    #    silenced: frozen stock (the clock is stopped) and items whose best-by
    #    is meaningless (ADR 0023 D4/D13).
    expiring = [
        (lot, item)
        for lot, item in lots
        if lot.expiration_date is not None
        and lot.expiration_date <= cutoff
        and not lot.frozen
        and not item.ignore_expiry
    ]
    for lot, item in sorted(expiring, key=lambda pair: pair[0].expiration_date):
        rows.append(_row(source="expiring", item=item, location_id=location_id, lot=lot))
        claimed_lots.add(lot.id)
        claimed_items.add(item.id)

    # 2. Open containers — started, so most likely to be finished next.
    opens = [(lot, item) for lot, item in lots if lot.status == "open" and lot.id not in claimed_lots]
    for lot, item in sorted(opens, key=lambda pair: (pair[0].expiration_date or date.max, pair[0].created_at)):
        rows.append(_row(source="open", item=item, location_id=location_id, lot=lot))
        claimed_lots.add(lot.id)
        claimed_items.add(item.id)

    # 3. Recently consumed and still in stock — the ordinary-usage feed. Ranked
    #    by how often the household actually reaches for it.
    since = datetime.now(UTC) - timedelta(days=RECENT_WINDOW_DAYS)
    recent = db.execute(
        select(InventoryEvent.item_id)
        .where(
            InventoryEvent.location_id == location_id,
            InventoryEvent.event_type == "consumed",
            InventoryEvent.occurred_at >= since,
        )
        .order_by(InventoryEvent.seq.desc())
    ).scalars()

    frequency: dict[UUID, int] = {}
    for item_id in recent:
        frequency[item_id] = frequency.get(item_id, 0) + 1

    in_stock = {
        balance.item_id: balance
        for balance in db.execute(
            select(InventoryBalance).where(
                InventoryBalance.location_id == location_id, InventoryBalance.quantity > 0
            )
        ).scalars()
    }
    items_by_id = {item.id: item for _lot, item in lots}

    for item_id, _count in sorted(frequency.items(), key=lambda pair: pair[1], reverse=True):
        if item_id in claimed_items or item_id not in in_stock:
            continue
        item = items_by_id.get(item_id) or db.get(Item, item_id)
        if item is None:
            continue
        balance = in_stock[item_id]
        rows.append(
            _row(
                source="recent",
                item=item,
                location_id=location_id,
                quantity=balance.quantity,
                unit=balance.unit,
            )
        )
        claimed_items.add(item_id)

    return rows[:limit]
