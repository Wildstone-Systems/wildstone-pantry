from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class PlateRequest(BaseModel):
    """Per-eater consumption. ``profile_id`` defaults to the active kiosk profile
    (the ``X-Household-Profile`` header); one or the other is required.

    ``location_id`` is optional: omit it for something eaten that never came out
    of pantry stock (a gas-station snack, a restaurant meal) — the calorie/macro
    snapshot is still logged and rolled up, just with no inventory to deduct
    from."""

    item_id: UUID
    location_id: UUID | None = None
    quantity: Decimal | None = Field(default=None, gt=0)
    unit: str = Field(default="each", min_length=1, max_length=24)
    weight_grams: Decimal | None = Field(default=None, gt=0)
    profile_id: UUID | None = None
    # Free-form grouping label (breakfast/lunch/dinner/snack). Never affects totals.
    meal_type: str | None = Field(default=None, max_length=24)


class EditPlateRequest(BaseModel):
    """Correct a plate's amount (the Calories page's edit button). Both fields
    default to the entry's current value when omitted, so editing just the
    unit (or just the quantity) is a one-field request."""

    quantity: Decimal | None = Field(default=None, gt=0)
    unit: str | None = Field(default=None, min_length=1, max_length=24)
    weight_grams: Decimal | None = Field(default=None, gt=0)


class PlateBatchEntry(BaseModel):
    item_id: UUID
    quantity: Decimal | None = Field(default=None, gt=0)
    unit: str = Field(default="each", min_length=1, max_length=24)
    weight_grams: Decimal | None = Field(default=None, gt=0)
    location_id: UUID | None = None
    meal_type: str | None = Field(default=None, max_length=24)
    note: str | None = None


class PlateBatchRequest(BaseModel):
    """Log several things in one confirmation. Validated as a whole first: if any
    entry is unloggable the batch fails with nothing written, so you never end up
    with half a meal recorded."""

    profile_id: UUID | None = None
    entries: list[PlateBatchEntry] = Field(min_length=1)


class PlateBatchResponse(BaseModel):
    profile_id: UUID
    logged: list[PlateResponse]
    total_calories: Decimal
    # False when any entry had no computable calories.
    complete: bool


class PlateRecipeRequest(BaseModel):
    """Log eating a portion of a recipe. Nutrition comes from the recipe's own
    ingredients scaled to ``servings``. The recipe's INGREDIENTS are never
    re-deducted (cooking already did that), but its declared OUTPUT is — eating
    the pulled pork takes it out of the fridge (ADR 0025 §5)."""

    recipe_id: UUID
    profile_id: UUID | None = None
    servings: Decimal = Field(default=Decimal(1), gt=0)
    note: str | None = None
    meal_type: str | None = Field(default=None, max_length=24)


class PlateRecipeResponse(BaseModel):
    event_id: UUID
    recipe_id: UUID
    item_id: UUID
    item_name: str
    profile_id: UUID
    servings: Decimal
    calories: Decimal | None
    protein_g: Decimal | None = None
    fat_g: Decimal | None = None
    carbs_g: Decimal | None = None
    # False when an ingredient had no nutrition data — the figures above are a
    # floor, not a total.
    complete: bool = True
    # What this meal took out of stock, and from where. Null when the recipe
    # declares no output item, none was stocked, or a serving couldn't be sized —
    # the meal is logged either way, and this is how a caller tells the two apart.
    deducted_quantity: Decimal | None = None
    deducted_unit: str | None = None
    location_id: UUID | None = None


class NutrientTotals(BaseModel):
    calories: Decimal
    protein_g: Decimal
    fat_g: Decimal
    carbs_g: Decimal


class NutritionDay(NutrientTotals):
    date: date


class NutritionSummaryResponse(BaseModel):
    from_: date = Field(alias="from")
    to: date
    # None = the whole household pooled.
    profile_id: UUID | None = None
    complete: bool
    days: list[NutritionDay]
    total: NutrientTotals
    daily_average: NutrientTotals

    model_config = ConfigDict(populate_by_name=True)


class PlateResponse(BaseModel):
    event_id: UUID
    item_id: UUID
    item_name: str
    # None on an off-pantry plate (no location_id given) — nothing was deducted.
    location_id: UUID | None
    profile_id: UUID
    quantity: Decimal
    unit: str
    calories: Decimal | None
    protein_g: Decimal | None = None
    fat_g: Decimal | None = None
    carbs_g: Decimal | None = None
    balance: Decimal | None


class CalorieEntryRead(BaseModel):
    event_id: UUID
    item_name: str
    meal_type: str | None = None
    quantity: Decimal
    unit: str
    # Only set when the plate carried an explicit plated weight (optional field
    # on /plate) — None for a plain "N servings/each" entry.
    weight_grams: Decimal | None = None
    calories: Decimal | None
    protein_g: Decimal | None = None
    fat_g: Decimal | None = None
    carbs_g: Decimal | None = None
    occurred_at: datetime


class CalorieRollupResponse(BaseModel):
    profile_id: UUID
    date: date
    # False when any entry lacked usable nutrition (its calories are null).
    complete: bool = True
    total_calories: Decimal
    total_protein_g: Decimal | None = None
    total_fat_g: Decimal | None = None
    total_carbs_g: Decimal | None = None
    target: Decimal | None
    entries: list[CalorieEntryRead]


class ProfileCalorieSummaryRead(BaseModel):
    profile_id: UUID
    display_name: str
    total_calories: Decimal
    total_protein_g: Decimal | None = None
    total_fat_g: Decimal | None = None
    total_carbs_g: Decimal | None = None
    target: Decimal | None


class CalorieSummaryResponse(BaseModel):
    date: date
    profiles: list[ProfileCalorieSummaryRead]
