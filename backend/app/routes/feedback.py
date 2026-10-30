from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.core.security import allow_kiosk, require_admin
from app.db import get_db
from app.schemas import FeedbackCreate, FeedbackListResponse, FeedbackRead, FeedbackUpdate
from app.services import create_feedback, list_feedback, update_feedback_status


# Kiosk-open: the feedback button is mounted globally on every page for any
# household member to use, with no login UI — only status-update (PATCH,
# below) is admin-gated. A no-credential request resolves to the shared
# personal identity, same as every other kiosk surface.
router = APIRouter(prefix="/feedback", tags=["feedback"], dependencies=[Depends(allow_kiosk)])


@router.post("", response_model=FeedbackRead)
def create_feedback_route(payload: FeedbackCreate, db: Session = Depends(get_db), user=Depends(allow_kiosk)) -> FeedbackRead:
    row = create_feedback(
        db,
        user_id=user.id,
        category=payload.category,
        route=payload.route,
        message=payload.message,
    )
    return FeedbackRead.model_validate(row)


@router.get("", response_model=FeedbackListResponse)
def list_feedback_route(
    limit: int = Query(default=50, ge=1, le=200),
    status: str | None = Query(default=None, max_length=24),
    db: Session = Depends(get_db),
    user=Depends(allow_kiosk),
) -> FeedbackListResponse:
    rows = list_feedback(db, user_id=None if user.role == "admin" else user.id, status=status, limit=limit)
    return FeedbackListResponse(feedback=[FeedbackRead.model_validate(row) for row in rows])


@router.patch("/{feedback_id}", response_model=FeedbackRead, dependencies=[Depends(require_admin)])
def update_feedback_route(
    feedback_id: UUID,
    payload: FeedbackUpdate,
    db: Session = Depends(get_db),
) -> FeedbackRead:
    try:
        row = update_feedback_status(db, feedback_id=feedback_id, status=payload.status)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return FeedbackRead.model_validate(row)
