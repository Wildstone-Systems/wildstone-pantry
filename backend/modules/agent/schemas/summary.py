from __future__ import annotations

from datetime import date

from pydantic import BaseModel


class AgentSummaryResponse(BaseModel):
    generated_on: date
    within_days: int
    locations: int
    expiring_soon: int
    expired: int
    low_stock_items: int
    shopping_pending: int
