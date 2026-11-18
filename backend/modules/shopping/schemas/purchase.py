from __future__ import annotations

from datetime import date, datetime
from uuid import UUID

from pydantic import BaseModel


class PurchaseLineRead(BaseModel):
    line_item_id: UUID
    raw_line: str
    parsed_name: str | None
    parsed_quantity: str
    parsed_unit: str
    parsed_barcode: str | None
    matched_item_id: UUID | None
    matched_item_name: str | None


class PurchaseRead(BaseModel):
    id: UUID
    merchant: str | None
    source: str
    purchased_on: date | None
    location_id: UUID | None
    status: str
    line_count: int
    created_at: datetime


class PurchaseListResponse(BaseModel):
    purchases: list[PurchaseRead]


class PurchaseDetailResponse(BaseModel):
    purchase: PurchaseRead
    lines: list[PurchaseLineRead]
