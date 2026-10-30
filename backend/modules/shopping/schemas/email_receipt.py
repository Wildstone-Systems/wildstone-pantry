"""Pydantic schemas for email receipt API endpoints."""

from __future__ import annotations

from pydantic import BaseModel, Field

# ---- Discover ----

class EmailRetailerRead(BaseModel):
    retailer: str
    senders: list[str]
    estimated_count: int
    latest_date: str | None = None


class EmailDiscoverResponse(BaseModel):
    retailers: list[EmailRetailerRead]
    error: str | None = None


# ---- List ----

class EmailReceiptSummary(BaseModel):
    message_id: str
    retailer: str | None = None
    sender: str
    subject: str
    date: str | None = None
    snippet: str


class EmailReceiptListRequest(BaseModel):
    retailer: str | None = None
    max_age_days: int = Field(default=30, ge=1, le=365)
    max_results: int = Field(default=20, ge=1, le=100)


class EmailReceiptListResponse(BaseModel):
    emails: list[EmailReceiptSummary]
    total_available: int = 0
    error: str | None = None


# ---- Preview ----

class EmailParsedLineItem(BaseModel):
    description: str
    product_name: str | None = None
    quantity: str = "1"
    unit: str = "each"
    price: str | None = None
    category: str | None = None


class EmailReceiptPreview(BaseModel):
    message_id: str
    retailer: str | None = None
    store_name: str | None = None
    store_number: str | None = None
    purchase_date: str | None = None
    subtotal: str | None = None
    tax: str | None = None
    total: str | None = None
    items: list[EmailParsedLineItem] = []
    item_count: int = 0
    parse_method: str = "generic"


class EmailReceiptPreviewBatchRequest(BaseModel):
    retailer: str | None = None
    max_age_days: int = Field(default=30, ge=1, le=365)
    max_results: int = Field(default=10, ge=1, le=50)


class EmailReceiptPreviewBatchResponse(BaseModel):
    previews: list[EmailReceiptPreview]
    errors: list[str] = []


# ---- Import ----

class EmailReceiptImportRequest(BaseModel):
    message_id: str
    location_id: str


class EmailReceiptImportResponse(BaseModel):
    receipt_id: str | None = None
    status: str = "error"
    item_count: int = 0
    inventory_transactions: list[str] = []
    error: str | None = None