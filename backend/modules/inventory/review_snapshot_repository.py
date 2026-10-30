"""Inventory-owned complete stock source and short confirmation locks."""

from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError

from app.models import Inventory
from modules.inventory.models import InventoryEvent


def stocked_item_ids(db):
    return list(db.scalars(select(Inventory.item_id).where(Inventory.quantity > 0).distinct()).all())


def snapshot(db, *, lock=False):
    if lock:
        if db.get_bind().dialect.name == "postgresql":
            # Prevent concurrent/phantom stock inserts as well as row updates.
            # Locks exist only for the short explicit confirmation transaction.
            try:
                # Writers use both event-first and balance-first paths. Never
                # wait holding one barrier for a writer holding the other.
                db.execute(text("LOCK TABLE inventory, inventory_events IN SHARE ROW EXCLUSIVE MODE NOWAIT"))
            except DBAPIError:
                raise ValueError("Stock changed during confirmation") from None
        else:
            # Acquire SQLite's write reservation without modifying a single row.
            db.execute(text("UPDATE inventory SET quantity = quantity WHERE 1 = 0"))
    rows = db.scalars(select(Inventory).order_by(Inventory.id)).all()
    return {
        "event_sequence": db.scalar(select(func.max(InventoryEvent.seq))) or 0,
        "rows": [
            {column.name: str(getattr(row, column.name)) for column in Inventory.__table__.columns} for row in rows
        ],
    }
