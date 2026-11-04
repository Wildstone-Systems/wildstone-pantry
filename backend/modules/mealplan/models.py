"""Tables owned by the mealplan module.

Registered in ``app.models`` so they are part of ``Base.metadata`` (Alembic +
test harness), the same arrangement as the planning module's tables.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy import CheckConstraint, Date, DateTime, ForeignKey, Index, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db_types import UUID
from app.models.base import Base

MEAL_TYPES = ("breakfast", "lunch", "dinner", "snack")
STATUSES = ("planned", "cooked", "skipped")
SOURCES = ("manual", "agent")


class MealPlanEntry(Base):
    """One planned meal on one day (epic #28).

    A household-wide entry has ``profile_id`` null; a per-person entry names
    who it is for. Several entries may share a day + meal type (a main and a
    side); ``position`` orders them.

    Recipe/item entries read their name live so they follow edits. ``title``
    is kept as the fallback: the FKs are ``SET NULL``, so deleting the recipe
    leaves the entry on the calendar under its last known name.
    """

    __tablename__ = "meal_plan_entries"
    __table_args__ = (
        CheckConstraint(
            "recipe_id IS NOT NULL OR item_id IS NOT NULL OR title IS NOT NULL",
            name="ck_meal_plan_entries_names_a_meal",
        ),
        CheckConstraint("servings >= 1", name="ck_meal_plan_entries_servings_positive"),
        Index("ix_meal_plan_entries_plan_date", "plan_date"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    plan_date: Mapped[date] = mapped_column(Date(), nullable=False)
    meal_type: Mapped[str] = mapped_column(String(16), nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")

    recipe_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("recipes.id", ondelete="SET NULL"), nullable=True, index=True
    )
    item_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("items.id", ondelete="SET NULL"), nullable=True
    )
    title: Mapped[str | None] = mapped_column(String(200), nullable=True)

    servings: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    profile_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("household_profiles.id", ondelete="SET NULL"), nullable=True
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="planned", server_default="planned")
    cook_event_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("recipe_cook_events.id", ondelete="SET NULL"), nullable=True
    )
    source: Mapped[str] = mapped_column(String(16), nullable=False, default="manual", server_default="manual")
    note: Mapped[str | None] = mapped_column(Text(), nullable=True)
    created_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class MealPlanExcludedItem(Base):
    """An ingredient the shopping bridge always skips (staples: salt, oil…), #31.

    ``key`` is ``canon:<canonical ingredient>`` when the ingredient has one (so
    every brand of salt is skipped) or ``item:<item id>`` otherwise.
    """

    __tablename__ = "meal_plan_excluded_items"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    key: Mapped[str] = mapped_column(String(200), nullable=False, unique=True)
    label: Mapped[str] = mapped_column(String(200), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class MealPlanCalendarFeed(Base):
    """The household's read-only iCal feed of the plan (#33).

    Calendar apps can't send headers, so the link itself carries a secret.
    Only its SHA-256 is stored; making a new link replaces the old one, and
    deleting the row turns the feed off.
    """

    __tablename__ = "meal_plan_calendar_feeds"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
