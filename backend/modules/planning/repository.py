"""Cook-along persistence: cook sessions and kitchen timers (ADR 0026 §6).

The only place the cook-along services touch the database. Commits stay in
the services, so one service call is still one unit of work.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.models import Recipe, RecipeCookEvent
from modules.planning.models import CookSession, CookTimer
from modules.planning.models.substitution_review import SubstitutionReview

# --- cook sessions ---------------------------------------------------------------


def lock_recipe_for_update(db: Session, recipe_id: UUID) -> Recipe | None:
    """Serialize ingredient replacement and review writes on their parent first."""
    return db.execute(
        select(Recipe).where(Recipe.id == recipe_id).with_for_update().execution_options(populate_existing=True)
    ).scalar_one_or_none()


def lock_recipe_dependents_nowait(db: Session, recipe_id: UUID) -> list[CookSession]:
    """Called with the recipe locked; never wait on sessions/terminal receipts."""
    sessions = list(
        db.scalars(
            select(CookSession)
            .where(CookSession.recipe_id == recipe_id)
            .order_by(CookSession.id)
            .with_for_update(nowait=True)
            .execution_options(populate_existing=True)
        ).all()
    )
    db.scalars(
        select(SubstitutionReview)
        .where(SubstitutionReview.recipe_id == recipe_id)
        .order_by(SubstitutionReview.id)
        .with_for_update(nowait=True)
        .execution_options(populate_existing=True)
    ).all()
    return sessions


def add(db: Session, row: CookSession | CookTimer) -> None:
    db.add(row)


def get_cook_session(db: Session, session_id: UUID) -> CookSession | None:
    return db.get(CookSession, session_id)


def latest_active_session(db: Session) -> CookSession | None:
    return db.execute(
        select(CookSession).where(CookSession.status == "active").order_by(CookSession.updated_at.desc()).limit(1)
    ).scalar_one_or_none()


def latest_finished_session(db: Session, *, ended_after: datetime) -> CookSession | None:
    return db.execute(
        select(CookSession)
        .where(CookSession.status == "finished", CookSession.ended_at >= ended_after)
        .order_by(CookSession.ended_at.desc())
        .limit(1)
    ).scalar_one_or_none()


def abandon_active_sessions(db: Session, *, now: datetime) -> None:
    db.execute(
        update(CookSession)
        .where(CookSession.status == "active")
        .values(status="abandoned", ended_at=now)
        .execution_options(synchronize_session=False)
    )


def update_active_session_at_version(
    db: Session, *, session_id: UUID, version: int, values: dict, now: datetime
) -> bool:
    """Write ``values`` only if the session is still active at ``version``
    (optimistic locking); bumps the version. True if it landed."""
    matched = db.execute(
        update(CookSession)
        .where(CookSession.id == session_id, CookSession.status == "active", CookSession.version == version)
        .values(**values, version=version + 1, updated_at=now)
        .execution_options(synchronize_session=False)
    ).rowcount
    return matched == 1


def claim_session_for_finish(db: Session, *, session_id: UUID, now: datetime) -> bool:
    """Move an active session to ``finishing``; True only for the caller whose
    update matched."""
    claimed = db.execute(
        update(CookSession)
        .where(CookSession.id == session_id, CookSession.status == "active")
        .values(status="finishing", updated_at=now)
    ).rowcount
    return claimed == 1


# --- kitchen timers --------------------------------------------------------------


def get_timer(db: Session, timer_id: UUID) -> CookTimer | None:
    return db.get(CookTimer, timer_id)


def running_timers(db: Session) -> list[CookTimer]:
    return list(db.execute(select(CookTimer).where(CookTimer.status == "running").order_by(CookTimer.ends_at)).scalars())


def timers_ending_in(db: Session, *, start: datetime, end: datetime) -> list[CookTimer]:
    """Timers still running or dismissed whose end falls in [start, end]."""
    return list(
        db.execute(
            select(CookTimer).where(
                CookTimer.status.in_(("running", "dismissed")),
                CookTimer.ends_at >= start,
                CookTimer.ends_at <= end,
            )
        ).scalars()
    )


# --- cook records (the integration feed) ------------------------------------------


def cook_events_with_recipe_since(db: Session, *, since: datetime, limit: int) -> list[tuple[RecipeCookEvent, str]]:
    """Cooks recorded at or after ``since`` with their recipe's name, oldest first."""
    rows = db.execute(
        select(RecipeCookEvent, Recipe.name)
        .join(Recipe, RecipeCookEvent.recipe_id == Recipe.id)
        .where(RecipeCookEvent.created_at >= since)
        .order_by(RecipeCookEvent.created_at.asc())
        .limit(limit)
    ).all()
    return [(event, name) for event, name in rows]
