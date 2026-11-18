"""Purchase history (read-only). Store hand-offs record purchases here."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.core.security import allow_kiosk
from app.db import get_db
from app.schemas import PurchaseDetailResponse, PurchaseListResponse, PurchaseRead
from app.services import get_purchase, list_purchase_lines, list_purchases

# Kiosk-open: the Purchases nav page has no login UI.
router = APIRouter(prefix="/purchases", tags=["purchases"], dependencies=[Depends(allow_kiosk)])


@router.get("", response_model=PurchaseListResponse)
def list_purchases_route(
    limit: int = Query(default=50, ge=1, le=200), db: Session = Depends(get_db)
) -> PurchaseListResponse:
    rows = list_purchases(db, limit=limit)
    return PurchaseListResponse(
        purchases=[
            PurchaseRead(
                id=row.id,
                merchant=row.merchant,
                source=row.source,
                purchased_on=row.purchased_on,
                location_id=row.location_id,
                status=row.status,
                line_count=row.line_count,
                created_at=row.created_at,
            )
            for row in rows
        ]
    )


@router.get("/{purchase_id}", response_model=PurchaseDetailResponse)
def purchase_detail_route(purchase_id: UUID, db: Session = Depends(get_db)) -> PurchaseDetailResponse:
    purchase = get_purchase(db, purchase_id)
    if not purchase:
        raise HTTPException(status_code=404, detail="Purchase not found")

    lines = list_purchase_lines(db, purchase_id)
    return PurchaseDetailResponse(
        purchase=PurchaseRead(
            id=purchase.id,
            merchant=purchase.merchant,
            source=purchase.source,
            purchased_on=purchase.purchased_on,
            location_id=purchase.location_id,
            status=purchase.status,
            line_count=purchase.line_count,
            created_at=purchase.created_at,
        ),
        lines=lines,
    )
