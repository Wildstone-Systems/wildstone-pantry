"""Human commands and bounded advisory responses for directional swaps."""

from datetime import datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from modules.catalog.services.units import to_recipe_unit


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SubstitutionContext(Strict):
    cooking_role: str | None = Field(default=None, max_length=200)
    preparation: str | None = Field(default=None, max_length=200)
    restrictions: str | None = Field(default=None, max_length=500)


class SubstitutionReviewCreate(Strict):
    ingredient_id: UUID
    substitute_item_id: UUID
    context: SubstitutionContext = Field(default_factory=SubstitutionContext)


class SubstitutionApproval(Strict):
    expected_binding: str = Field(min_length=64, max_length=64)
    confirmed: Literal[True]
    ratio: Decimal = Field(gt=0, le=Decimal("999999.9999"), allow_inf_nan=False)
    unit: str = Field(min_length=1, max_length=24)
    scope: Literal["one_use", "recipe", "global"] = "one_use"
    permanent_ack: bool = False
    replace_existing_ack: bool = False
    replace_approval_ack: bool = False

    @field_validator("ratio")
    @classmethod
    def stored_ratio(cls, value):
        rounded = value.quantize(Decimal("0.0001"))
        if rounded <= 0:
            raise ValueError("Ratio rounds to zero at storage precision")
        if rounded != value:
            raise ValueError("Use at most four decimal places; explicitly review any rounded ratio")
        return rounded

    @field_validator("unit")
    @classmethod
    def known_unit(cls, value):
        unit = to_recipe_unit(value)
        if not unit:
            raise ValueError("Choose a supported substitute unit")
        return unit


class SubstitutionAttach(Strict):
    expected_binding: str = Field(min_length=64, max_length=64)
    confirmed: Literal[True]
    replace_approval_ack: bool = False


class SubstitutionReviewRead(BaseModel):
    id: UUID
    session_id: UUID
    recipe_id: UUID
    ingredient_id: UUID
    from_item_id: UUID
    to_item_id: UUID
    binding: str
    status: str
    scope: str | None
    ratio: str | None
    from_unit: str
    unit: str | None
    context: SubstitutionContext
    snapshot: dict
    missing_evidence: list[str]
    evaluation: dict | None
    existing_global: dict | None
    stale: bool
    warning: str
    approved_at: datetime | None
    consumed_at: datetime | None


class SubstitutionReviewList(BaseModel):
    reviews: list[SubstitutionReviewRead]
    recipe_approvals: list[SubstitutionReviewRead]
    attached_ids: list[UUID]
    missing_attachment_ids: list[UUID] = Field(default_factory=list)
