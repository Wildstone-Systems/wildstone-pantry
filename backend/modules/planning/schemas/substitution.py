from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class SubstitutionCreate(BaseModel):
    from_item_id: UUID
    to_item_id: UUID
    ratio: Decimal | None = Field(default=None, gt=0)
    unit: str | None = Field(default=None, max_length=24)
    outcome: str = Field(default="acceptable", max_length=16)
    notes: str | None = None


class SubstitutionUpdate(BaseModel):
    ratio: Decimal | None = Field(default=None, gt=0)
    unit: str | None = Field(default=None, max_length=24)
    outcome: str | None = Field(default=None, max_length=16)
    notes: str | None = None


class SubstitutionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    from_item_id: UUID
    to_item_id: UUID
    ratio: Decimal
    unit: str
    from_unit: str | None = None
    outcome: str
    notes: str | None
    created_at: datetime


class SubstitutionListResponse(BaseModel):
    substitutions: list[SubstitutionRead]
