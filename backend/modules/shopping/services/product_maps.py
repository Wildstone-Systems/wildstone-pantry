"""Retailer product mappings — the cross-trip memory (ADR 0019 D4).

Writers all already have the data in hand: handoff completion
(``source=handoff``), Kroger cart pushes (``source=cart_push``), and offer
picking (``source=offer_pick``). ``chosen_offer`` on a shopping entry stays
the per-trip snapshot; this table is what makes the NEXT trip smarter.
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import RetailerProductMap


def upsert_mapping(
    db: Session,
    *,
    item_id: UUID,
    retailer_key: str,
    external_id: str,
    source: str,
    external_name: str | None = None,
    external_url: str | None = None,
    upc: str | None = None,
    size: str | None = None,
    commit: bool = True,
) -> RetailerProductMap:
    """Record (or re-confirm) that item X at retailer R is product P.

    Re-confirming bumps ``confirmed_count`` and fills gaps (name/url/upc/size
    are never overwritten by a sparser later write)."""
    external_id = external_id.strip()
    row = db.execute(
        select(RetailerProductMap).where(
            RetailerProductMap.item_id == item_id,
            RetailerProductMap.retailer_key == retailer_key,
            RetailerProductMap.external_id == external_id,
        )
    ).scalar_one_or_none()
    if row is None:
        row = RetailerProductMap(
            item_id=item_id,
            retailer_key=retailer_key,
            external_id=external_id,
            external_name=external_name,
            external_url=external_url,
            upc=upc,
            size=size,
            source=source,
        )
        db.add(row)
    else:
        row.confirmed_count += 1
        row.last_confirmed_at = datetime.now(timezone.utc)
        row.external_name = row.external_name or external_name
        row.external_url = row.external_url or external_url
        row.upc = row.upc or upc
        row.size = row.size or size
    if commit:
        db.commit()
        db.refresh(row)
    return row


def best_mapping(db: Session, *, item_id: UUID, retailer_key: str) -> RetailerProductMap | None:
    """The household's strongest belief: highest confirmed_count, most recent
    confirmation as the tiebreak."""
    return db.execute(
        select(RetailerProductMap)
        .where(RetailerProductMap.item_id == item_id, RetailerProductMap.retailer_key == retailer_key)
        .order_by(RetailerProductMap.confirmed_count.desc(), RetailerProductMap.last_confirmed_at.desc())
        .limit(1)
    ).scalar_one_or_none()


def best_link(db: Session, *, adapter, item_id: UUID | None, name: str) -> tuple[str | None, str]:
    """Resolve "what should the user open for this item at this retailer" —
    the ADR 0019 D4 hierarchy as it applies to a link adapter:

    1. confirmed mapping with a product URL → open the product page directly
    2. confirmed mapping's external name → search for the exact store product
    3. raw name search (the plain handoff query)

    Returns (url, query_used). The feature gets better every trip."""
    if item_id is not None:
        mapping = best_mapping(db, item_id=item_id, retailer_key=adapter.key)
        if mapping is not None:
            if mapping.external_url:
                return mapping.external_url, mapping.external_name or name
            product = adapter.product_url(db, mapping.external_id)
            if product:
                return product, mapping.external_name or name
            if mapping.external_name:
                return adapter.search_url(db, mapping.external_name), mapping.external_name
    return adapter.search_url(db, name), name
