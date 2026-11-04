"""Meal plan request/response models (epic #28, #29)."""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

MealType = Literal["breakfast", "lunch", "dinner", "snack"]
EntryStatus = Literal["planned", "cooked", "skipped"]


class MealPlanEntryCreate(BaseModel):
    """Exactly one of ``recipe_id`` / ``item_id`` / ``title`` names the meal."""

    model_config = ConfigDict(extra="forbid")

    plan_date: date
    meal_type: MealType
    recipe_id: UUID | None = None
    item_id: UUID | None = None
    title: str | None = Field(default=None, max_length=200)
    # Omitted → the recipe's own servings (or 1). Pinned on the entry after that.
    servings: int | None = Field(default=None, ge=1, le=100)
    profile_id: UUID | None = None
    note: str | None = Field(default=None, max_length=2000)


class MealPlanEntryUpdate(BaseModel):
    """Partial update. Sending a field as null clears it (where it may be null).

    Changing ``plan_date`` or ``meal_type`` without a ``position`` moves the
    entry to the end of its new cell.
    """

    model_config = ConfigDict(extra="forbid")

    plan_date: date | None = None
    meal_type: MealType | None = None
    position: int | None = Field(default=None, ge=0)
    recipe_id: UUID | None = None
    item_id: UUID | None = None
    title: str | None = Field(default=None, max_length=200)
    servings: int | None = Field(default=None, ge=1, le=100)
    profile_id: UUID | None = None
    note: str | None = Field(default=None, max_length=2000)


class MealPlanEntryRead(BaseModel):
    id: UUID
    plan_date: date
    meal_type: MealType
    position: int
    kind: Literal["recipe", "item", "text"]
    # What to show: the live recipe/item name, or the title fallback.
    meal: str
    recipe_id: UUID | None
    item_id: UUID | None
    title: str | None
    servings: int
    profile_id: UUID | None
    status: EntryStatus
    cook_event_id: UUID | None
    source: Literal["manual", "agent"]
    note: str | None
    created_by: str | None
    created_at: datetime
    updated_at: datetime


class MealPlanWeek(BaseModel):
    start: date
    days: int
    entries: list[MealPlanEntryRead]
