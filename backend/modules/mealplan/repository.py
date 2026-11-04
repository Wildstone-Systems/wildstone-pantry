"""Meal plan persistence. The only place the mealplan module touches the database.

Commits stay in the services, so one service call is one unit of work.
"""

from __future__ import annotations

from datetime import date
from uuid import UUID

from sqlalchemy import delete as sa_delete
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from modules.mealplan.models import MealPlanCalendarFeed, MealPlanEntry, MealPlanExcludedItem


def get_entry(db: Session, entry_id: UUID) -> MealPlanEntry | None:
    return db.get(MealPlanEntry, entry_id)


def list_entries(db: Session, *, start: date, end: date) -> list[MealPlanEntry]:
    """Entries with ``start <= plan_date < end``, in calendar order."""
    return list(
        db.scalars(
            select(MealPlanEntry)
            .where(MealPlanEntry.plan_date >= start, MealPlanEntry.plan_date < end)
            .order_by(MealPlanEntry.plan_date, MealPlanEntry.meal_type, MealPlanEntry.position, MealPlanEntry.created_at)
        ).all()
    )


def cell_entries(db: Session, *, plan_date: date, meal_type: str) -> list[MealPlanEntry]:
    """Entries in one day + meal type cell, in display order."""
    return list(
        db.scalars(
            select(MealPlanEntry)
            .where(MealPlanEntry.plan_date == plan_date, MealPlanEntry.meal_type == meal_type)
            .order_by(MealPlanEntry.position, MealPlanEntry.created_at)
        ).all()
    )


def next_position(db: Session, *, plan_date: date, meal_type: str) -> int:
    """One past the last position in a day + meal type cell (0 when empty)."""
    last = db.scalar(
        select(func.max(MealPlanEntry.position)).where(
            MealPlanEntry.plan_date == plan_date, MealPlanEntry.meal_type == meal_type
        )
    )
    return 0 if last is None else last + 1


def fingerprint_rows(db: Session) -> list[tuple]:
    """Every entry's identity and last change, in a stable order (#33)."""
    return list(
        db.execute(
            select(
                MealPlanEntry.id,
                MealPlanEntry.plan_date,
                MealPlanEntry.meal_type,
                MealPlanEntry.position,
                MealPlanEntry.status,
                MealPlanEntry.updated_at,
            ).order_by(MealPlanEntry.id)
        ).all()
    )


def add(db: Session, entry: MealPlanEntry) -> MealPlanEntry:
    db.add(entry)
    return entry


def delete(db: Session, entry: MealPlanEntry) -> None:
    db.delete(entry)


# --- always-skipped ingredients (#31) -------------------------------------------


def list_excluded(db: Session) -> list[MealPlanExcludedItem]:
    return list(db.scalars(select(MealPlanExcludedItem).order_by(MealPlanExcludedItem.label)).all())


def get_excluded(db: Session, excluded_id: UUID) -> MealPlanExcludedItem | None:
    return db.get(MealPlanExcludedItem, excluded_id)


def get_excluded_by_key(db: Session, key: str) -> MealPlanExcludedItem | None:
    return db.scalar(select(MealPlanExcludedItem).where(MealPlanExcludedItem.key == key))


# --- calendar feed (#33) --------------------------------------------------------


def calendar_feed(db: Session) -> MealPlanCalendarFeed | None:
    return db.scalar(select(MealPlanCalendarFeed).order_by(MealPlanCalendarFeed.created_at.desc()))


def calendar_feed_by_hash(db: Session, token_hash: str) -> MealPlanCalendarFeed | None:
    return db.scalar(select(MealPlanCalendarFeed).where(MealPlanCalendarFeed.token_hash == token_hash))


def replace_calendar_feed(db: Session, token_hash: str) -> MealPlanCalendarFeed:
    db.execute(sa_delete(MealPlanCalendarFeed))
    feed = MealPlanCalendarFeed(token_hash=token_hash)
    db.add(feed)
    return feed


def delete_calendar_feed(db: Session) -> None:
    db.execute(sa_delete(MealPlanCalendarFeed))
