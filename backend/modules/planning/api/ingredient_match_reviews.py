"""Deliberate authenticated human review; no kiosk/agent authority fallback."""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.orm import Session

from app.core.security import deny_agent_keys, require_authenticated_admin
from app.db import get_db
from modules.planning.schemas.ingredient_match_review import (
    ConfirmMatchReview,
    CreateMatchReview,
    MatchCandidateList,
    MatchReviewList,
    MatchReviewRead,
    ReviewBinding,
)
from modules.planning.services import ingredient_match_reviews as service

router = APIRouter(tags=["ingredient match review"], dependencies=[Depends(deny_agent_keys)])


def call(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except LookupError:
        raise HTTPException(404, "Recipe ingredient or active candidate unavailable") from None
    except ValueError:
        raise HTTPException(
            409, "Review changed, expired or prerequisites are unresolved; refresh and review again"
        ) from None


@router.get("/recipes/{recipe_id}/ingredients/{ingredient_id}/match-candidates", response_model=MatchCandidateList)
def candidates(
    recipe_id: UUID,
    ingredient_id: UUID,
    q: str = Query(default="", max_length=200),
    db: Session = Depends(get_db),
    actor=Depends(require_authenticated_admin),
):
    return call(service.candidates, db, recipe_id, ingredient_id, query=q)


@router.get("/recipes/{recipe_id}/ingredients/{ingredient_id}/match-reviews", response_model=MatchReviewList)
def history(
    recipe_id: UUID, ingredient_id: UUID, db: Session = Depends(get_db), actor=Depends(require_authenticated_admin)
):
    return call(service.history, db, recipe_id, ingredient_id, actor.id)


@router.post(
    "/recipes/{recipe_id}/ingredients/{ingredient_id}/match-reviews",
    response_model=MatchReviewRead,
)
def create(
    recipe_id: UUID,
    ingredient_id: UUID,
    payload: CreateMatchReview,
    db: Session = Depends(get_db),
    actor=Depends(require_authenticated_admin),
):
    return call(service.create, db, recipe_id, ingredient_id, payload, actor.id)


@router.get(
    "/ingredient-match-reviews/{review_id}",
    response_model=MatchReviewRead,
    response_model_exclude={"confirmation_token"},
)
def read(review_id: UUID, db: Session = Depends(get_db), actor=Depends(require_authenticated_admin)):
    return call(service.read, db, review_id, actor.id)


@router.post(
    "/ingredient-match-reviews/{review_id}/compare",
    response_model=MatchReviewRead,
    response_model_exclude={"confirmation_token"},
)
async def compare(
    review_id: UUID,
    payload: ReviewBinding,
    request: Request,
    db: Session = Depends(get_db),
    actor=Depends(require_authenticated_admin),
):
    try:
        return await service.compare(db, review_id, payload, actor.id, request.is_disconnected)
    except LookupError:
        raise HTTPException(404, "Review unavailable") from None
    except ValueError:
        raise HTTPException(
            409, "Review changed, expired or prerequisites are unresolved; refresh and review again"
        ) from None


@router.post(
    "/ingredient-match-reviews/{review_id}/confirm",
    response_model=MatchReviewRead,
    response_model_exclude={"confirmation_token"},
)
def confirm(
    review_id: UUID,
    payload: ConfirmMatchReview,
    db: Session = Depends(get_db),
    actor=Depends(require_authenticated_admin),
):
    return call(service.confirm, db, review_id, payload, actor.id)


@router.post(
    "/ingredient-match-reviews/{review_id}/reject",
    response_model=MatchReviewRead,
    response_model_exclude={"confirmation_token"},
)
def reject(
    review_id: UUID, payload: ReviewBinding, db: Session = Depends(get_db), actor=Depends(require_authenticated_admin)
):
    return call(service.reject, db, review_id, payload, actor.id)
