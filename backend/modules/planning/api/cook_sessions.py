"""Cook sessions: step-by-step cooking a person or an agent can drive (ADR 0026 §6).

Kiosk-open like the rest of cooking: the kitchen tablet has no login, and an
agent key keeps its own scope enforcement through ``allow_kiosk``.
"""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Response
from fastapi.encoders import jsonable_encoder
from sqlalchemy.orm import Session

from app.core.security import allow_kiosk, resolve_actor
from app.db import get_db
from modules.planning.schemas.cook_session import (
    CookSessionFinish,
    CookSessionFinished,
    CookSessionRead,
    CookSessionStart,
    CookSessionStep,
    CookSessionStepDone,
    CookSessionUpdate,
    CookTimerRead,
    CookTimerStart,
    CookTimerStopped,
)
from modules.planning.services import cook_sessions as service
from modules.planning.services import cook_timers

router = APIRouter(prefix="/cook-sessions", tags=["cook-sessions"], dependencies=[Depends(allow_kiosk)])
# Kitchen timers (ADR 0026 §6.1): kiosk-open like the cook-along they serve.
timers_router = APIRouter(prefix="/cook-timers", tags=["cook-timers"], dependencies=[Depends(allow_kiosk)])


def _read(db: Session, session) -> CookSessionRead:
    return CookSessionRead.model_validate(service.view(db, session))


@router.post("", response_model=CookSessionRead, status_code=201)
def start_cook_session(
    payload: CookSessionStart,
    db: Session = Depends(get_db),
    actor: str | None = Depends(resolve_actor),
) -> CookSessionRead:
    """Start cooking a recipe. Any other active session is abandoned."""
    try:
        session = service.start_session(
            db,
            recipe_id=payload.recipe_id,
            servings_made=payload.servings_made,
            actor=actor,
            plan=payload.plan.model_dump(mode="json") if payload.plan else None,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _read(db, session)


@router.get("/active", response_model=CookSessionRead, responses={204: {"description": "Nothing is being cooked"}})
def active_cook_session(db: Session = Depends(get_db)):
    session = service.get_active(db)
    if session is None:
        return Response(status_code=204)
    return _read(db, session)


@router.get("/{session_id}", response_model=CookSessionRead)
def get_cook_session(session_id: UUID, db: Session = Depends(get_db)) -> CookSessionRead:
    try:
        return _read(db, service.get_session(db, session_id))
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.patch("/{session_id}", response_model=CookSessionRead)
def update_cook_session(
    session_id: UUID, payload: CookSessionUpdate, db: Session = Depends(get_db)
) -> CookSessionRead:
    """Change the servings or the plan of an active cook; progress is kept."""
    changes = {}
    if "servings_made" in payload.model_fields_set:
        changes["servings_made"] = payload.servings_made
    if "plan" in payload.model_fields_set:
        changes["plan"] = payload.plan.model_dump(mode="json") if payload.plan else None
        if payload.plan and "substitution_approval_ids" not in payload.plan.model_fields_set:
            changes["plan"].pop("substitution_approval_ids", None)
    try:
        session = service.update_session(db, session_id=session_id, **changes)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _read(db, session)


@router.post("/{session_id}/steps/{step}", response_model=CookSessionRead)
def set_cook_step_done(
    session_id: UUID, step: int, payload: CookSessionStepDone, db: Session = Depends(get_db)
) -> CookSessionRead:
    """Tick a step off (or back on) without moving to it."""
    try:
        session = service.set_step_done(db, session_id=session_id, step=step, done=payload.done)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _read(db, session)


@router.post("/{session_id}/step", response_model=CookSessionRead)
def move_cook_session_step(
    session_id: UUID, payload: CookSessionStep, db: Session = Depends(get_db)
) -> CookSessionRead:
    try:
        session = service.move_step(db, session_id=session_id, action=payload.action, step=payload.step)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _read(db, session)


@router.post("/{session_id}/finish", response_model=CookSessionFinished)
def finish_cook_session(
    session_id: UUID,
    payload: CookSessionFinish,
    db: Session = Depends(get_db),
    actor: str | None = Depends(resolve_actor),
) -> CookSessionFinished:
    """Finish cooking: takes the ingredients out of stock (the only step that does)."""
    try:
        session, result = service.finish_session(
            db,
            session_id=session_id,
            location_id=payload.location_id,
            actor=actor,
            output=payload.model_dump(exclude={"location_id"}, exclude_none=True),
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return CookSessionFinished(session=_read(db, session), cook=jsonable_encoder(result))


@router.delete("/{session_id}", response_model=CookSessionRead)
def abandon_cook_session(session_id: UUID, db: Session = Depends(get_db)) -> CookSessionRead:
    """Stop cooking without touching stock."""
    try:
        return _read(db, service.abandon_session(db, session_id=session_id))
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@timers_router.get("", response_model=list[CookTimerRead])
def list_cook_timers(db: Session = Depends(get_db)) -> list[CookTimerRead]:
    """Every timer running or ringing, soonest first."""
    return [CookTimerRead.model_validate(t) for t in cook_timers.list_timers(db)]


@timers_router.post("", response_model=CookTimerRead, status_code=201)
def start_cook_timer(payload: CookTimerStart, db: Session = Depends(get_db)) -> CookTimerRead:
    try:
        timer = cook_timers.start_timer(
            db,
            seconds=payload.seconds,
            label=payload.label,
            session_id=payload.session_id,
            step_number=payload.step_number,
            set_by_profile_id=payload.profile_id,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return CookTimerRead.model_validate(cook_timers.view(timer))


@timers_router.delete("/{timer_id}", response_model=CookTimerStopped)
def stop_cook_timer(timer_id: UUID, db: Session = Depends(get_db)) -> CookTimerStopped:
    """Cancel a timer before it goes off, or dismiss one that is ringing."""
    try:
        return CookTimerStopped.model_validate(cook_timers.stop_timer(db, timer_id=timer_id))
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
