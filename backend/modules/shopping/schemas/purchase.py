from __future__ import annotations

from datetime import date, datetime
from uuid import UUID

from pydantic import BaseModel, Field


class PurchaseImportCsvRequest(BaseModel):
    location_id: UUID = Field(..., description="Inventory location to ingest into.")
    csv_text: str = Field(..., min_length=1, description="Raw CSV text (with or without headers).")
    merchant: str | None = Field(default=None, description="Optional merchant label for the import (e.g. Instacart).")
    purchased_on: date | None = Field(default=None, description="Optional purchase date (YYYY-MM-DD).")
    create_missing_items: bool = Field(default=True, description="Create items that cannot be matched.")
    apply_to_inventory: bool = Field(default=True, description="If true, adds inventory transactions for each line.")
    default_unit: str = Field(default="each", description="Unit to use when CSV does not include one.")


class PurchaseLineRead(BaseModel):
    line_item_id: UUID
    raw_line: str
    parsed_name: str | None
    parsed_quantity: str
    parsed_unit: str
    parsed_barcode: str | None
    matched_item_id: UUID | None
    matched_item_name: str | None


class PurchaseImportResponse(BaseModel):
    purchase_id: UUID
    status: str
    line_count: int
    created_item_ids: list[UUID]
    inventory_transaction_ids: list[UUID]
    lines: list[PurchaseLineRead]
    created_at: datetime


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


class PurchasePreviewCsvRequest(BaseModel):
    location_id: UUID = Field(..., description="Inventory location to preview against.")
    csv_text: str = Field(..., min_length=1, description="Raw CSV text (with or without headers).")
    merchant: str | None = Field(default=None, description="Optional merchant label used for fingerprinting.")
    purchased_on: date | None = Field(default=None, description="Optional purchase date used for fingerprinting (YYYY-MM-DD).")
    create_missing_items: bool = Field(default=True, description="If true, preview will mark missing items as 'would create'.")
    apply_to_inventory: bool = Field(default=True, description="If true, preview will mark lines as 'would add'.")
    default_unit: str = Field(default="each", description="Unit to use when CSV does not include one.")


class PurchasePreviewLineRead(BaseModel):
    line_item_id: UUID
    raw_line: str
    parsed_name: str | None
    parsed_quantity: str
    parsed_unit: str
    parsed_barcode: str | None
    matched_item_id: UUID | None
    matched_item_name: str | None
    would_create_item: bool
    would_add_to_inventory: bool


class PurchasePreviewResponse(BaseModel):
    fingerprint: str
    duplicate_of_purchase_id: UUID | None
    line_count: int
    would_create_item_names: list[str]
    lines: list[PurchasePreviewLineRead]
