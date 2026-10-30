from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.core.security import allow_kiosk
from app.db import get_db
from modules.planning.schemas.insights import (
    ConsumptionTrendRead,
    ConsumptionTrendResponse,
    ExpiringItemRead,
    ExpiringItemResponse,
    RestockSuggestionRead,
    RestockSuggestionResponse,
)
from modules.planning.services.insights import build_consumption_trends, build_restock_suggestions, list_expiring_items

# Kiosk-open: the Insights nav page (trends/restock/expiring) has no login UI
# and is read-only over the household's own pantry data.
router = APIRouter(prefix="/insights", tags=["insights"], dependencies=[Depends(allow_kiosk)])


@router.get("/trends/{location_id}", response_model=ConsumptionTrendResponse)
def trends_route(
    location_id: UUID,
    lookback_days: int = Query(default=30, ge=7, le=180),
    db: Session = Depends(get_db),
) -> ConsumptionTrendResponse:
    try:
        rows = build_consumption_trends(db, location_id=location_id, lookback_days=lookback_days)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    return ConsumptionTrendResponse(
        location_id=location_id,
        trends=[ConsumptionTrendRead.model_validate(row) for row in rows],
    )


@router.get("/restock/{location_id}", response_model=RestockSuggestionResponse)
def restock_route(location_id: UUID, db: Session = Depends(get_db)) -> RestockSuggestionResponse:
    try:
        rows = build_restock_suggestions(db, location_id=location_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    return RestockSuggestionResponse(
        location_id=location_id,
        suggestions=[RestockSuggestionRead.model_validate(row) for row in rows],
    )


@router.get("/expiring/{location_id}", response_model=ExpiringItemResponse)
def expiring_route(
    location_id: UUID,
    within_days: int = Query(default=7, ge=1, le=365),
    db: Session = Depends(get_db),
) -> ExpiringItemResponse:
    try:
        rows = list_expiring_items(db, location_id=location_id, within_days=within_days)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    return ExpiringItemResponse(
        location_id=location_id,
        expiring_items=[ExpiringItemRead.model_validate(row) for row in rows],
    )
