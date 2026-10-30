"""Retailer adapter registry (ADR 0019 D1).

Sits beside the existing connector registries (it composes them, replaces
nothing): every ``retailer_links`` row becomes an adapter, with code-backed
classes (Kroger, Instacart) overlaying API capabilities on their row. Adding
a search-link retailer is a data change; adding an API retailer is a class in
``adapters.CODE_ADAPTERS``.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import RetailerLink
from modules.shopping.retailers.adapters import CODE_ADAPTERS, LinkRetailerAdapter
from modules.shopping.retailers.seed import ensure_seed


def list_adapters(db: Session) -> list[LinkRetailerAdapter]:
    ensure_seed(db)
    rows = db.execute(select(RetailerLink).order_by(RetailerLink.label.asc())).scalars().all()
    return [CODE_ADAPTERS.get(row.key, LinkRetailerAdapter)(row) for row in rows]


def get_adapter(db: Session, key: str) -> LinkRetailerAdapter | None:
    ensure_seed(db)
    row = db.execute(select(RetailerLink).where(RetailerLink.key == key)).scalar_one_or_none()
    if row is None:
        return None
    return CODE_ADAPTERS.get(row.key, LinkRetailerAdapter)(row)
