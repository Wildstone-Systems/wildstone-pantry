"""Cook session request/response models (ADR 0026 §6)."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from modules.planning.schemas.recipe import RecipeCookIngredientOverride, RecipeCookSubstitution


class CookPlan(BaseModel):
    """How a cook was set up (ADR 0022 §2, ADR 0025 §5/§7). Finishing the
    session cooks exactly this, whoever finishes it."""

    model_config = ConfigDict(extra="forbid")

    ingredient_overrides: list[RecipeCookIngredientOverride] = Field(default_factory=list)
    scale_to_ingredient_id: UUID | None = None
    allow_short_ingredients: bool = False
    substitutions: list[RecipeCookSubstitution] = Field(default_factory=list)
    substitution_approval_ids: list[UUID] = Field(default_factory=list, max_length=8)


class CookSessionStart(BaseModel):
    recipe_id: UUID
    servings_made: Decimal | None = Field(
        default=None,
        gt=0,
        description="Absolute servings to make (ADR 0022 §1). Omit to cook the recipe as written.",
    )
    plan: CookPlan | None = None


class CookSessionUpdate(BaseModel):
    """Only the fields sent are changed."""

    servings_made: Decimal | None = Field(default=None, gt=0)
    plan: CookPlan | None = None


class CookSessionStep(BaseModel):
    action: Literal["next", "previous", "repeat", "goto", "complete"]
    step: int | None = Field(
        default=None, description="1-based step number; required for goto, optional for complete."
    )


class CookSessionStepDone(BaseModel):
    done: bool = True


class CookSessionFinish(BaseModel):
    location_id: UUID | None = Field(
        default=None, description="Where to take stock from; omitted = pooled across locations."
    )
    # What only the finish knows (ADR 0025 §3/§4): what the dish weighed and
    # how it was packed. Blank = not measured / not packed.
    output_weight_grams: Decimal | None = Field(default=None, gt=0)
    output_portions: int | None = Field(default=None, ge=1)
    output_portion_size: Decimal | None = Field(default=None, gt=0)
    output_portion_unit: str | None = Field(default=None, max_length=24)


class CookTimerStart(BaseModel):
    seconds: int = Field(ge=1, le=24 * 60 * 60)
    label: str | None = Field(default=None, max_length=80)
    session_id: UUID | None = None
    step_number: int | None = Field(default=None, ge=1)
    # The household profile picked on the tablet ("Who's using this?"), so
    # the timer's "done" can reach that person (ADR 0026 §6.1).
    profile_id: UUID | None = None


class CookTimerRead(BaseModel):
    id: UUID
    label: str
    session_id: UUID | None
    step_number: int | None
    duration_seconds: int
    started_at: datetime
    ends_at: datetime
    remaining_seconds: int
    state: Literal["running", "ringing"]


class CookTimerStopped(CookTimerRead):
    stopped: Literal["cancelled", "dismissed"]


class CookStep(BaseModel):
    number: int
    text: str
    done: bool


class SuggestedTimer(BaseModel):
    seconds: int
    label: str


class CookSessionIngredient(BaseModel):
    ingredient_id: str
    item: str
    quantity: str
    unit: str
    optional: bool = False
    # Set on a substituted line: the recipe's own ingredient it stands in for.
    replaces: str | None = None


class CookSessionRead(BaseModel):
    id: UUID
    recipe_id: UUID
    recipe_name: str
    status: str
    base_servings: int | None
    servings_made: Decimal | None
    step_number: int
    total_steps: int
    step_text: str
    is_first_step: bool
    is_last_step: bool
    step_ingredients: list[CookSessionIngredient]
    step_done: bool
    step_timers: list[SuggestedTimer]
    steps: list[CookStep]
    completed_steps: list[int]
    steps_left: int
    timers: list[CookTimerRead]
    plan: dict | None
    ingredients: list[CookSessionIngredient]
    cook_event_id: UUID | None
    started_at: datetime
    updated_at: datetime
    scoped_substitutions: list[dict] = Field(default_factory=list)
    substitution_review_warning: str | None = None


class CookSessionFinished(BaseModel):
    session: CookSessionRead
    cook: dict
