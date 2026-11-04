"""Meal plan routes: ``/api/meal-plan`` (epic #28, #29).

Kiosk-open: anyone in the household can edit the plan. Agent keys may plan
too (their writes still need the ``write`` scope) and are recorded as
``source=agent``.
"""

from __future__ import annotations

from datetime import date
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Response
from sqlalchemy.orm import Session

from app.core.security import allow_kiosk, resolve_actor
from app.db import get_db
from modules.mealplan import services, shopping_gap
from modules.mealplan.schemas import (
    ExcludedItemCreate,
    ExcludedItemRead,
    MealPlanEntryCreate,
    MealPlanEntryRead,
    MealPlanEntryUpdate,
    MealPlanWeek,
    ShoppingGap,
    ShoppingGapPush,
    ShoppingGapPushResult,
)

router = APIRouter(prefix="/meal-plan", tags=["meal-plan"], dependencies=[Depends(allow_kiosk)])


def _run(db: Session, fn, *args, **kwargs):
    try:
        return fn(db, *args, **kwargs)
    except LookupError as exc:
        db.rollback()
        raise HTTPException(404, str(exc)) from exc
    except services.MealPlanStateError as exc:
        db.rollback()
        raise HTTPException(409, str(exc)) from exc
    except (services.MealPlanInputError, shopping_gap.ShoppingGapInputError) as exc:
        db.rollback()
        raise HTTPException(422, str(exc)) from exc


@router.get("/entries", response_model=MealPlanWeek)
def list_entries(
    start: date = Query(..., description="First day (YYYY-MM-DD); the UI sends a Sunday"),
    days: int = Query(7, ge=1, le=services.MAX_DAYS),
    db: Session = Depends(get_db),
):
    return _run(db, services.list_week, start=start, days=days)


@router.post("/entries", response_model=MealPlanEntryRead, status_code=201)
def create_entry(
    payload: MealPlanEntryCreate,
    db: Session = Depends(get_db),
    user=Depends(allow_kiosk),
    profile_actor: str | None = Depends(resolve_actor),
    x_agent_key: str | None = Header(default=None, alias="X-Agent-Key"),
):
    actor = profile_actor or str(user.id)
    source = "agent" if x_agent_key else "manual"
    return _run(db, services.create, payload, actor=actor, source=source)


@router.get("/entries/{entry_id}", response_model=MealPlanEntryRead)
def get_entry(entry_id: UUID, db: Session = Depends(get_db)):
    return _run(db, services.get, entry_id)


@router.patch("/entries/{entry_id}", response_model=MealPlanEntryRead)
def update_entry(entry_id: UUID, payload: MealPlanEntryUpdate, db: Session = Depends(get_db)):
    return _run(db, services.update, entry_id, payload)


@router.delete("/entries/{entry_id}", status_code=204)
def delete_entry(entry_id: UUID, db: Session = Depends(get_db)):
    _run(db, services.remove, entry_id)
    return Response(status_code=204)


@router.post("/entries/{entry_id}/skip", response_model=MealPlanEntryRead)
def skip_entry(entry_id: UUID, db: Session = Depends(get_db)):
    return _run(db, services.skip, entry_id)


@router.post("/entries/{entry_id}/reset", response_model=MealPlanEntryRead)
def reset_entry(entry_id: UUID, db: Session = Depends(get_db)):
    return _run(db, services.reset, entry_id)


# --- shopping bridge (#31) -------------------------------------------------------


@router.get("/shopping-gap", response_model=ShoppingGap)
def get_shopping_gap(
    start: date = Query(..., description="First day of the week shown (YYYY-MM-DD)"),
    today: date = Query(..., description="The viewer's local today; earlier days are ignored"),
    days: int = Query(7, ge=1, le=services.MAX_DAYS),
    db: Session = Depends(get_db),
):
    return _run(db, shopping_gap.compute, start=start, days=days, today=today)


@router.post("/shopping-gap/push", response_model=ShoppingGapPushResult)
def push_shopping_gap(payload: ShoppingGapPush, db: Session = Depends(get_db)):
    return _run(
        db, shopping_gap.push, start=payload.start, days=payload.days, today=payload.today, keys=payload.keys
    )


@router.get("/excluded-items", response_model=list[ExcludedItemRead])
def list_excluded_items(db: Session = Depends(get_db)):
    return _run(db, shopping_gap.list_excluded)


@router.post("/excluded-items", response_model=ExcludedItemRead, status_code=201)
def add_excluded_item(payload: ExcludedItemCreate, db: Session = Depends(get_db)):
    return _run(db, shopping_gap.exclude, key=payload.key, label=payload.label)


@router.delete("/excluded-items/{excluded_id}", status_code=204)
def remove_excluded_item(excluded_id: UUID, db: Session = Depends(get_db)):
    _run(db, shopping_gap.unexclude, excluded_id)
    return Response(status_code=204)
