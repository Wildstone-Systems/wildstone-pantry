from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.models import ConsumptionTrend, InventoryLocation, Item, RestockSuggestion

# Import the event-sourcing projection models from their real home (the inventory
# module), NOT via app.models — app.models.__init__ imports these mid-init, so
# pulling them through app.models here creates a circular import in some import
# orders (e.g. a standalone Base.metadata.create_all). See shopping_list.py.
from modules.inventory.models import InventoryBalance, InventoryEvent, InventoryLot

# Insights are derived from the inventory event ledger (ADR 0002): on-hand from
# the `inv_balance` projection, usage from `consumed` events, and expiry from
# per-lot `inv_lot` dates — not the legacy `inventory`/`inventory_transactions`.


def _require_location(db: Session, location_id: UUID) -> None:
    location = db.execute(select(InventoryLocation).where(InventoryLocation.id == location_id)).scalar_one_or_none()
    if not location:
        raise LookupError("Location not found")


def build_consumption_trends(db: Session, *, location_id: UUID, lookback_days: int = 30) -> list[dict]:
    _require_location(db, location_id)

    cutoff = date.today() - timedelta(days=lookback_days)
    # Usage = `consumed` events at this location within the window (true
    # consumption, unlike the legacy sum that also counted transfers).
    event_rows = db.execute(
        select(InventoryEvent).where(
            InventoryEvent.location_id == location_id,
            InventoryEvent.event_type == "consumed",
            InventoryEvent.occurred_at >= cutoff,
        )
    ).scalars().all()

    consumed_by_item: dict[UUID, Decimal] = {}
    for row in event_rows:
        consumed_by_item[row.item_id] = consumed_by_item.get(row.item_id, Decimal("0")) + abs(Decimal(row.quantity))

    balance_rows = db.execute(
        select(InventoryBalance).where(InventoryBalance.location_id == location_id)
    ).scalars().all()
    inventory_by_item = {row.item_id: Decimal(row.quantity) for row in balance_rows}

    item_names = {
        row.id: row.name
        for row in db.execute(select(Item).where(Item.id.in_(list(inventory_by_item.keys()) + list(consumed_by_item.keys())))).scalars().all()
    }

    db.execute(delete(ConsumptionTrend).where(ConsumptionTrend.location_id == location_id))
    db.commit()

    trends: list[dict] = []
    for item_id, consumed in consumed_by_item.items():
        avg_daily = consumed / Decimal(lookback_days)
        current_qty = inventory_by_item.get(item_id, Decimal("0"))
        days_to_depletion = (current_qty / avg_daily) if avg_daily > 0 else None

        model = ConsumptionTrend(
            item_id=item_id,
            location_id=location_id,
            avg_daily_usage=avg_daily,
            days_to_depletion=days_to_depletion,
            sampled_days=lookback_days,
        )
        db.add(model)
        db.commit()
        db.refresh(model)

        trends.append(
            {
                "item_id": item_id,
                "item_name": item_names.get(item_id, "Unknown"),
                "avg_daily_usage": float(avg_daily),
                "days_to_depletion": float(days_to_depletion) if days_to_depletion is not None else None,
                "sampled_days": lookback_days,
            }
        )

    trends.sort(key=lambda row: row["avg_daily_usage"], reverse=True)
    return trends


def build_restock_suggestions(db: Session, *, location_id: UUID) -> list[dict]:
    _require_location(db, location_id)

    trends = build_consumption_trends(db, location_id=location_id)
    balance_rows = db.execute(
        select(InventoryBalance).where(InventoryBalance.location_id == location_id)
    ).scalars().all()
    inventory_by_item = {row.item_id: Decimal(row.quantity) for row in balance_rows}

    db.execute(delete(RestockSuggestion).where(RestockSuggestion.location_id == location_id))
    db.commit()

    suggestions: list[dict] = []
    for trend in trends:
        item_id = trend["item_id"]
        item_name = trend["item_name"]
        current_qty = float(inventory_by_item.get(item_id, Decimal("0")))
        days_to_depletion = trend["days_to_depletion"]

        should_restock = current_qty <= 1 or (days_to_depletion is not None and days_to_depletion <= 7)
        if not should_restock:
            continue

        priority = 3 if current_qty <= 0 else 2 if current_qty <= 1 else 1
        reason = "low_stock" if current_qty <= 1 else "depleting_soon"
        recommendation = f"Restock {item_name} soon (qty={current_qty:.2f})."

        row = RestockSuggestion(
            item_id=item_id,
            location_id=location_id,
            priority=priority,
            reason=reason,
            recommendation=recommendation,
        )
        db.add(row)
        db.commit()
        db.refresh(row)

        suggestions.append(
            {
                "item_id": item_id,
                "item_name": item_name,
                "current_quantity": current_qty,
                "priority": priority,
                "reason": reason,
                "recommendation": recommendation,
            }
        )

    suggestions.sort(key=lambda row: row["priority"], reverse=True)
    return suggestions


def list_expiring_items(db: Session, *, location_id: UUID | None, within_days: int = 7) -> list[dict]:
    """Lots expiring within ``within_days``. ``location_id=None`` means every
    location (the household-wide view integrations use — ADR 0026)."""
    if location_id is not None:
        _require_location(db, location_id)

    cutoff = date.today() + timedelta(days=within_days)
    # Per-lot expiry from the ledger projection (finer-grained than the legacy
    # single expiration_date per item/location).
    #
    # Two exclusions keep the feed worth reading (ADR 0023 D13, D4). Freezing is
    # how a household says "stop the clock", so a frozen lot must not keep
    # counting down; and an item flagged `ignore_expiry` (spices, condiments)
    # has no meaningful best-by at all. A list that nags about either teaches
    # people to ignore the whole list, which costs more than the rows it shows.
    rows = db.execute(
        select(InventoryLot, Item)
        .join(Item, InventoryLot.item_id == Item.id)
        .where(
            *([InventoryLot.location_id == location_id] if location_id is not None else []),
            InventoryLot.expiration_date.is_not(None),
            InventoryLot.expiration_date <= cutoff,
            InventoryLot.quantity_remaining > 0,
            InventoryLot.frozen.is_(False),
            Item.ignore_expiry.is_(False),
        )
        .order_by(InventoryLot.expiration_date.asc())
    ).all()

    out: list[dict] = []
    for lot, item in rows:
        days = (lot.expiration_date - date.today()).days
        out.append(
            {
                "item_id": lot.item_id,
                "item_name": item.name,
                "expiration_date": lot.expiration_date,
                "days_until_expiration": days,
                # Lot identity so a row's buttons can act on the actual
                # container instead of re-guessing FEFO (ADR 0023 D10).
                "lot_id": lot.id,
                "location_id": lot.location_id,
                "quantity_remaining": lot.quantity_remaining,
                "unit": lot.unit,
                "status": lot.status,
            }
        )

    return out
