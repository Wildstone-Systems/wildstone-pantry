"""Explicit human review commands; evaluation has no write authority."""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy.orm import Session

from app.core.security import allow_kiosk, deny_agent_keys, require_authenticated_admin
from app.db import get_db
from modules.planning.schemas.substitution_review import (
    SubstitutionApproval,
    SubstitutionAttach,
    SubstitutionReviewCreate,
    SubstitutionReviewList,
    SubstitutionReviewRead,
)
from modules.planning.services import substitution_reviews as service

router = APIRouter(tags=["substitution-review"], dependencies=[Depends(allow_kiosk)])


def command(db, fn, *args):
    try:
        return fn(db, *args)
    except LookupError as exc:
        db.rollback()
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        db.rollback()
        raise HTTPException(409, str(exc)) from exc


@router.get("/cook-sessions/{session_id}/substitution-reviews", response_model=SubstitutionReviewList)
def list_reviews(session_id: UUID, db: Session = Depends(get_db)):
    return command(db, service.list_reviews, session_id)


@router.post(
    "/cook-sessions/{session_id}/substitution-reviews",
    response_model=SubstitutionReviewRead,
    dependencies=[Depends(deny_agent_keys)],
)
def create_review(
    session_id: UUID, payload: SubstitutionReviewCreate, db: Session = Depends(get_db), actor=Depends(allow_kiosk)
):
    return command(db, service.create, session_id, payload, actor.id)


@router.post(
    "/substitution-reviews/{review_id}/evaluate",
    response_model=SubstitutionReviewRead,
    dependencies=[Depends(deny_agent_keys)],
)
def evaluate_review(review_id: UUID, db: Session = Depends(get_db), actor=Depends(require_authenticated_admin)):
    return command(db, service.evaluate, review_id)


@router.post(
    "/substitution-reviews/{review_id}/approve",
    response_model=SubstitutionReviewRead,
    dependencies=[Depends(deny_agent_keys)],
)
def approve_review(
    review_id: UUID, payload: SubstitutionApproval, db: Session = Depends(get_db), actor=Depends(allow_kiosk)
):
    return command(db, service.approve, review_id, payload, actor.id)


@router.post(
    "/cook-sessions/{session_id}/substitution-reviews/{review_id}/attach",
    response_model=SubstitutionReviewRead,
    dependencies=[Depends(deny_agent_keys)],
)
def attach_review(session_id: UUID, review_id: UUID, payload: SubstitutionAttach, db: Session = Depends(get_db)):
    return command(db, service.attach, session_id, review_id, payload)


@router.delete("/substitution-reviews/{review_id}", status_code=204, dependencies=[Depends(deny_agent_keys)])
def revoke_review(review_id: UUID, db: Session = Depends(get_db)):
    command(db, service.revoke, review_id)
    return Response(status_code=204)
