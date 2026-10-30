"""Versioned CSV draft choices; source binding is checked by the service."""

from datetime import date
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

from modules.shopping.schemas.purchase import PurchasePreviewCsvRequest
from modules.shopping.schemas.receipt_review import DraftLine, ReceiptAnnotationRead, ReceiptClassifierReviewRead


class PurchaseDraftRequest(PurchasePreviewCsvRequest):
    csv_text: str = Field(min_length=1, max_length=64000)
    merchant: str | None = Field(default=None, max_length=120)
    default_unit: str = Field(default="each", max_length=16)


class PurchaseDraftLine(DraftLine):
    barcode: str | None = Field(default=None, max_length=64)


class PurchaseDraftAction(BaseModel):
    token: str = Field(min_length=20, max_length=200)
    fingerprint: str = Field(min_length=64, max_length=64)
    lines: list[PurchaseDraftLine] = Field(min_length=1, max_length=200)


class PurchaseDraftLineRead(PurchaseDraftLine):
    source_quantity: str
    source_nonstock: bool
    matched_item_name: str | None = None
    matched_item_brand: str | None = None
    resolution: Literal["barcode", "name_or_canonical", "unresolved"]
    warnings: list[str]
    annotation: ReceiptAnnotationRead | None = None


class PurchaseDraftRead(BaseModel):
    version: Literal["purchase-review.v1"]
    draft_id: UUID
    token: str
    fingerprint: str
    source_fingerprint: str
    location_id: UUID
    merchant: str | None
    purchased_on: date | None
    create_missing_items: bool
    apply_to_inventory: bool
    default_unit: str
    duplicate_of_purchase_id: UUID | None
    lines: list[PurchaseDraftLineRead]
    line_count: int
    warning: str


class PurchaseClassifierReviewRead(ReceiptClassifierReviewRead):
    version: Literal["purchase-review.v1"]
