"""Meal plan persistence. The only place the mealplan module touches the database.

Commits stay in the services, so one service call is one unit of work.
"""

from __future__ import annotations

from datetime import date
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from modules.mealplan.models import MealPlanEntry


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


def next_position(db: Session, *, plan_date: date, meal_type: str) -> int:
    """One past the last position in a day + meal type cell (0 when empty)."""
    last = db.scalar(
        select(func.max(MealPlanEntry.position)).where(
            MealPlanEntry.plan_date == plan_date, MealPlanEntry.meal_type == meal_type
        )
    )
    return 0 if last is None else last + 1


def add(db: Session, entry: MealPlanEntry) -> MealPlanEntry:
    db.add(entry)
    return entry


def delete(db: Session, entry: MealPlanEntry) -> None:
    db.delete(entry)
