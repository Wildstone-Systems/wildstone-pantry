from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class ProfileCreateRequest(BaseModel):
    display_name: str = Field(min_length=1, max_length=120)
    account_id: UUID | None = None
    daily_calorie_target: Decimal | None = Field(default=None, ge=0)


class ProfileUpdateRequest(BaseModel):
    display_name: str | None = Field(default=None, min_length=1, max_length=120)
    account_id: UUID | None = None
    daily_calorie_target: Decimal | None = Field(default=None, ge=0)
    is_active: bool | None = None


class ProfileRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    display_name: str
    account_id: UUID | None
    daily_calorie_target: Decimal | None
    has_pin: bool
    is_active: bool
    created_at: datetime


class ProfileListResponse(BaseModel):
    profiles: list[ProfileRead]


class SetPinRequest(BaseModel):
    # None or empty clears the PIN. Digits only, 4-12 chars when set.
    pin: str | None = None


class VerifyPinRequest(BaseModel):
    pin: str = Field(min_length=1)


class VerifyPinResponse(BaseModel):
    valid: bool
