from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class IngredientReferenceCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    category: str | None = Field(default=None, max_length=100)
    density_g_per_ml: Decimal | None = Field(default=None, gt=0)
    grams_per_each: Decimal | None = Field(default=None, gt=0)
    notes: str | None = None


class IngredientReferenceUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    category: str | None = Field(default=None, max_length=100)
    density_g_per_ml: Decimal | None = Field(default=None, gt=0)
    grams_per_each: Decimal | None = Field(default=None, gt=0)
    notes: str | None = None


class IngredientReferenceRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    category: str | None
    density_g_per_ml: Decimal | None
    grams_per_each: Decimal | None
    notes: str | None
    created_at: datetime


class IngredientReferenceListResponse(BaseModel):
    references: list[IngredientReferenceRead]
