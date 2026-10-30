"""Catalog-owned bounded evidence reads and row-lock authority."""

from sqlalchemy import select, text

from app.models import Item


def identity(db, item_id):
    """Identity metadata only, retaining archived rows for legacy mapping reads."""
    return db.execute(select(Item).where(Item.id == item_id)).scalar_one_or_none()


def get(db, item_id, *, lock=False):
    if lock and db.get_bind().dialect.name == "sqlite":
        db.execute(text("UPDATE items SET name = name WHERE id = :key"), {"key": item_id.hex})
    statement = select(Item).where(Item.id == item_id, Item.is_active.is_(True))
    if lock:
        statement = statement.with_for_update()
    return db.execute(statement.execution_options(populate_existing=True)).scalar_one_or_none()
