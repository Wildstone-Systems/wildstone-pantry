from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.core.security import allow_kiosk, deny_agent_keys, require_authenticated_admin
from app.db import get_db
from app.schemas import (
    PurchaseDetailResponse,
    PurchaseImportCsvRequest,
    PurchaseImportResponse,
    PurchaseListResponse,
    PurchasePreviewCsvRequest,
    PurchasePreviewResponse,
    PurchaseRead,
)
from app.services import (
    get_purchase,
    import_purchase_csv_text,
    list_purchase_lines,
    list_purchases,
    preview_purchase_csv_text,
)
from modules.shopping.schemas.purchase_review import (
    PurchaseClassifierReviewRead,
    PurchaseDraftAction,
    PurchaseDraftRead,
    PurchaseDraftRequest,
)
from modules.shopping.services import purchase_review

# Kiosk-open: the Purchases nav page (CSV import/history) has no login UI.
router = APIRouter(prefix="/purchases", tags=["purchases"], dependencies=[Depends(allow_kiosk)])


@router.post("/draft-csv", response_model=PurchaseDraftRead)
def purchase_draft_route(payload: PurchaseDraftRequest, db: Session = Depends(get_db), actor=Depends(allow_kiosk)):
    try:
        return purchase_review.create_draft(db, actor_id=actor.id, payload=payload.model_dump(mode="json"))
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.post(
    "/drafts/{draft_id}/review", response_model=PurchaseClassifierReviewRead, dependencies=[Depends(deny_agent_keys)]
)
def purchase_classifier_route(
    draft_id: UUID,
    payload: PurchaseDraftAction,
    db: Session = Depends(get_db),
    actor=Depends(require_authenticated_admin),
):
    try:
        return purchase_review.review_draft(
            db,
            draft_id=draft_id,
            actor_id=actor.id,
            token=payload.token,
            source_fingerprint=payload.fingerprint,
            lines=[line.model_dump(mode="json") for line in payload.lines],
        )
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.post(
    "/drafts/{draft_id}/commit", response_model=PurchaseImportResponse, dependencies=[Depends(deny_agent_keys)]
)
def purchase_commit_route(
    draft_id: UUID, payload: PurchaseDraftAction, db: Session = Depends(get_db), actor=Depends(allow_kiosk)
):
    try:
        return purchase_review.commit_draft(
            db,
            draft_id=draft_id,
            actor_id=actor.id,
            token=payload.token,
            source_fingerprint=payload.fingerprint,
            lines=[line.model_dump(mode="json") for line in payload.lines],
        )
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.post("/import-csv", response_model=PurchaseImportResponse)
def import_purchase_csv_route(
    payload: PurchaseImportCsvRequest, db: Session = Depends(get_db)
) -> PurchaseImportResponse:
    try:
        result = import_purchase_csv_text(
            db,
            csv_text=payload.csv_text,
            location_id=payload.location_id,
            merchant=payload.merchant,
            purchased_on=payload.purchased_on,
            create_missing_items=payload.create_missing_items,
            apply_to_inventory=payload.apply_to_inventory,
            default_unit=payload.default_unit,
        )
        return PurchaseImportResponse.model_validate(result)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/preview-csv", response_model=PurchasePreviewResponse)
def preview_purchase_csv_route(
    payload: PurchasePreviewCsvRequest, db: Session = Depends(get_db)
) -> PurchasePreviewResponse:
    try:
        result = preview_purchase_csv_text(
            db,
            csv_text=payload.csv_text,
            location_id=payload.location_id,
            merchant=payload.merchant,
            purchased_on=payload.purchased_on,
            create_missing_items=payload.create_missing_items,
            apply_to_inventory=payload.apply_to_inventory,
            default_unit=payload.default_unit,
        )
        return PurchasePreviewResponse.model_validate(result)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


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
