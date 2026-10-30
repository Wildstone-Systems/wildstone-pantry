from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, field_validator, model_validator


class ReceiptIngestRequest(BaseModel):
    location_id: UUID
    raw_text: str = Field(min_length=1)
    source_filename: str | None = None


class ReceiptLineRead(BaseModel):
    line_item_id: UUID
    raw_line: str
    parsed_name: str | None
    parsed_quantity: str
    parsed_unit: str
    matched_item_id: UUID | None
    matched_item_name: str | None


class ReceiptIngestResponse(BaseModel):
    receipt_id: UUID
    status: str
    line_count: int
    inventory_transaction_ids: list[UUID]
    lines: list[ReceiptLineRead]
    created_at: datetime


class ReceiptPreviewRequest(BaseModel):
    location_id: UUID
    raw_text: str = Field(min_length=1)
    source_filename: str | None = None


class ReceiptPreviewLine(BaseModel):
    raw_line: str
    name: str
    quantity: str
    unit: str
    matched_item_id: UUID | None = None
    matched_item_name: str | None = None
    expiration_days_estimate: int | None = None
    # Provenance (ADR 0018 §9): AI-derived lines are visibly drafts. "rules"
    # lines came from the deterministic parser; "ai" lines from a provider.
    source: str = "rules"
    confidence: float | None = None


class ReceiptPreviewResponse(BaseModel):
    lines: list[ReceiptPreviewLine]
    line_count: int
    # Which engine produced the preview: "rules" or an AI provider key.
    engine: str = "rules"


class ReceiptCommitLine(BaseModel):
    raw_line: str
    name: str = Field(min_length=1, max_length=200)
    quantity: Decimal = Field(gt=0, max_digits=12, decimal_places=3, allow_inf_nan=False)
    unit: Literal["each", "lb", "oz", "g", "kg", "ml", "l"]
    matched_item_id: UUID | None = None
    create_new: bool = False

    @field_validator("name", mode="before")
    @classmethod
    def trim_name(cls, value):
        return value.strip() if isinstance(value, str) else value

    @field_validator("unit", mode="before")
    @classmethod
    def normalize_unit(cls, value):
        if isinstance(value, str):
            value = value.strip().lower()
            return "each" if value == "ea" else value
        return value

    @model_validator(mode="after")
    def require_item_choice(self):
        if (self.matched_item_id is not None) == self.create_new:
            raise ValueError("Choose an existing item or explicitly create a new item")
        return self


class ReceiptCommitRequest(BaseModel):
    location_id: UUID
    source_filename: str | None = None
    raw_text: str | None = None
    lines: list[ReceiptCommitLine] = Field(min_length=1)


class ReceiptCommitResponse(BaseModel):
    receipt_id: UUID
    status: str
    line_count: int
    inventory_transaction_ids: list[UUID]
    lines: list[ReceiptLineRead]
    created_at: datetime
