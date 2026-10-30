"""Short catalog barrier prevents identity-source and retrieval phantoms."""

from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError

from app.models import Item


def confirmed_candidates(db, key, stocked_ids):
    if not key or not stocked_ids:
        return []
    return list(
        db.scalars(
            select(Item)
            .where(
                Item.is_active.is_(True),
                Item.canonical_ingredient == key,
                Item.canonical_provenance == "confirmed",
                Item.id.in_(stocked_ids),
            )
            .order_by(Item.name, Item.id)
            .limit(3)
        ).all()
    )


def lock_source_scope(db):
    if db.get_bind().dialect.name == "postgresql":
        try:
            # NOWAIT fails closed instead of waiting while another writer holds
            # a different table/row; no dependence on its mutation lock order.
            db.execute(text("LOCK TABLE items, item_product_evidence IN SHARE ROW EXCLUSIVE MODE NOWAIT"))
        except DBAPIError:
            raise ValueError("Catalog changed during confirmation") from None
    else:
        db.execute(text("UPDATE items SET name = name WHERE 1 = 0"))
