"""Purchase history reads. Purchases are written by the store hand-off flow
(``services/handoff.py``); this module only lists them."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Item, Purchase, PurchaseLineItem


def list_purchases(db: Session, *, limit: int = 50) -> list[Purchase]:
    capped = max(1, min(int(limit), 200))
    return list(db.execute(select(Purchase).order_by(Purchase.created_at.desc()).limit(capped)).scalars().all())


def get_purchase(db: Session, purchase_id: UUID) -> Purchase | None:
    return db.execute(select(Purchase).where(Purchase.id == purchase_id)).scalar_one_or_none()


def list_purchase_lines(db: Session, purchase_id: UUID) -> list[dict]:
    rows = db.execute(
        select(PurchaseLineItem, Item)
        .join(Item, PurchaseLineItem.matched_item_id == Item.id, isouter=True)
        .where(PurchaseLineItem.purchase_id == purchase_id)
        .order_by(PurchaseLineItem.created_at.asc())
    ).all()

    out: list[dict] = []
    for line, item in rows:
        out.append(
            {
                "line_item_id": line.id,
                "raw_line": line.raw_line,
                "parsed_name": line.parsed_name,
                "parsed_quantity": str(line.parsed_quantity),
                "parsed_unit": line.parsed_unit,
                "parsed_barcode": line.parsed_barcode,
                "matched_item_id": line.matched_item_id,
                "matched_item_name": item.name if item else None,
            }
        )
    return out
