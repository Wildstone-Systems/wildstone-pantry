"""Explicit human-only ranked identity review; no inference from GET routes."""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.core.security import deny_agent_keys, require_authenticated_admin
from app.db import get_db
from modules.planning.schemas.ranked_ingredient_review import (
    ConfirmRankedAlias,
    EvaluateIngredientMatches,
    RankedReviewBinding,
    RankedReviewRead,
)
from modules.planning.services import ranked_ingredient_reviews as service

router = APIRouter(
    prefix="/recipes/ingredient-matches",
    tags=["ranked ingredient identity review"],
    dependencies=[Depends(deny_agent_keys)],
)


def call(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except LookupError:
        raise HTTPException(404, "Recipe ingredient or review unavailable") from None
    except ValueError:
        args[0].rollback()
        raise HTTPException(409, "Review changed, expired or is not a same-ingredient choice; evaluate again") from None


@router.post("/evaluate", response_model=RankedReviewRead)
async def evaluate(
    payload: EvaluateIngredientMatches,
    request: Request,
    db: Session = Depends(get_db),
    actor=Depends(require_authenticated_admin),
):
    try:
        return await service.evaluate(db, payload, actor.id, request.is_disconnected)
    except LookupError:
        raise HTTPException(404, "Recipe ingredient unavailable") from None
    except ValueError:
        db.rollback()
        raise HTTPException(409, "Review changed or capacity reached; refresh and evaluate again") from None


@router.get("/reviews/{review_id}", response_model=RankedReviewRead, response_model_exclude={"confirmation_token"})
def read(review_id: UUID, db: Session = Depends(get_db), actor=Depends(require_authenticated_admin)):
    return call(service.read, db, review_id, actor.id)


@router.post(
    "/reviews/{review_id}/confirm", response_model=RankedReviewRead, response_model_exclude={"confirmation_token"}
)
def confirm(
    review_id: UUID,
    payload: ConfirmRankedAlias,
    db: Session = Depends(get_db),
    actor=Depends(require_authenticated_admin),
):
    return call(service.confirm, db, review_id, payload, actor.id)


@router.post(
    "/reviews/{review_id}/reject", response_model=RankedReviewRead, response_model_exclude={"confirmation_token"}
)
def reject(
    review_id: UUID,
    payload: RankedReviewBinding,
    db: Session = Depends(get_db),
    actor=Depends(require_authenticated_admin),
):
    return call(service.reject, db, review_id, payload, actor.id)
