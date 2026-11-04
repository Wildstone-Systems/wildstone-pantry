"""Meal plan request/response models (epic #28, #29)."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
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


class MealPlanEntryCooked(BaseModel):
    model_config = ConfigDict(extra="forbid")

    cook_event_id: UUID


class MealPlanWeek(BaseModel):
    start: date
    days: int
    entries: list[MealPlanEntryRead]


# --- shopping bridge (#31) -------------------------------------------------------


class ShoppingGapLine(BaseModel):
    # canon:<ingredient> or item:<item id>; what "always skip" and push refer to.
    key: str
    label: str
    unit: str
    needed: Decimal
    on_hand: Decimal | None
    # None when the amount can't be measured (no unit bridge): check by hand.
    to_buy: Decimal | None
    status: Literal["short", "check"]
    meals: list[str]


class ExcludedItemRead(BaseModel):
    id: UUID
    key: str
    label: str


class ShoppingGap(BaseModel):
    # The window actually used: today onward, inside the requested week.
    start: date
    end: date
    lines: list[ShoppingGapLine]
    excluded: list[ExcludedItemRead]


class ShoppingGapPush(BaseModel):
    model_config = ConfigDict(extra="forbid")

    start: date
    days: int = Field(default=7, ge=1, le=42)
    today: date
    keys: list[str] = Field(default_factory=list, max_length=500)


class ShoppingGapPushResult(BaseModel):
    added: list[str]
    already_on_list: list[str]


class ExcludedItemCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str = Field(max_length=200)
    label: str = Field(max_length=200)


# --- nutrition forecast (#34) ----------------------------------------------------


class ForecastDay(BaseModel):
    date: date
    calories: int
    # Some planned recipe lacks nutrition data for an ingredient: the real
    # number is higher than shown.
    partial: bool
    # Planned meals with no calorie data at all (free text, unknown items).
    unknown_meals: int
    meals: int


class ForecastPerson(BaseModel):
    profile_id: UUID
    display_name: str
    target: float | None
    days: list[ForecastDay]


class NutritionForecast(BaseModel):
    start: date
    days: int
    people: list[ForecastPerson]
