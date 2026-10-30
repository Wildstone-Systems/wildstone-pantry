from __future__ import annotations

from datetime import date
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel


class ConsumptionTrendRead(BaseModel):
    item_id: UUID
    item_name: str
    avg_daily_usage: float
    days_to_depletion: float | None
    sampled_days: int


class ConsumptionTrendResponse(BaseModel):
    location_id: UUID
    trends: list[ConsumptionTrendRead]


class RestockSuggestionRead(BaseModel):
    item_id: UUID
    item_name: str
    current_quantity: float
    priority: int
    reason: str
    recommendation: str


class RestockSuggestionResponse(BaseModel):
    location_id: UUID
    suggestions: list[RestockSuggestionRead]


class ExpiringItemRead(BaseModel):
    item_id: UUID
    item_name: str
    expiration_date: date
    days_until_expiration: int
    # Which container this row is about (ADR 0023 D10). Without it a "used" or
    # "threw it out" tap can only fall back to FEFO — right by luck when the row
    # happens to be the soonest-expiring lot, silently wrong otherwise — and it
    # could never say how much was involved.
    lot_id: UUID | None = None
    quantity_remaining: Decimal | None = None
    unit: str | None = None
    status: str | None = None


class ExpiringItemResponse(BaseModel):
    location_id: UUID
    expiring_items: list[ExpiringItemRead]
