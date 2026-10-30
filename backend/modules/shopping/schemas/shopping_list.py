from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, Field


class ShoppingListAddRequest(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    item_id: UUID | None = None
    quantity: Decimal = Field(default=Decimal("1"), gt=0)
    unit: str = Field(default="each", min_length=1, max_length=24)
    note: str | None = None


class ShoppingListUpdateRequest(BaseModel):
    quantity: Decimal | None = Field(default=None, gt=0)
    status: str | None = None  # pending | purchased
    note: str | None = None


class OfferSelectRequest(BaseModel):
    """Pick a searched offer → create/link the catalog item + record the snapshot."""

    provider: str = Field(min_length=1, max_length=32)
    name: str = Field(min_length=1, max_length=200)
    vendor: str | None = None
    vendor_sku: str | None = None
    upc: str | None = Field(default=None, max_length=32)
    brand: str | None = None
    size: str | None = None
    price: float | None = Field(default=None, ge=0)
    image_url: str | None = None
    quantity: Decimal = Field(default=Decimal("1"), gt=0)
    unit: str = Field(default="each", min_length=1, max_length=24)
    entry_id: UUID | None = None  # resolve an existing loose need, else add new
    # Attach the offer to an EXISTING catalog item (e.g. a recipe's ingredient)
    # instead of creating a new one — enriches that item and links to it, so
    # shopping for a recipe's ingredients doesn't spawn duplicate catalog items.
    item_id: UUID | None = None


class ShoppingListEntryRead(BaseModel):
    id: UUID
    item_id: UUID | None
    name: str
    quantity: Decimal
    unit: str
    source: str
    status: str
    note: str | None
    chosen_offer: dict | None = None
    on_hand: Decimal | None
    created_at: datetime
    updated_at: datetime


class ShoppingListResponse(BaseModel):
    items: list[ShoppingListEntryRead]


class ExportTargetRead(BaseModel):
    key: str
    label: str
    configured: bool


class ExportTargetsResponse(BaseModel):
    targets: list[ExportTargetRead]


class KrogerCartLine(BaseModel):
    upc: str
    quantity: int
    name: str


class KrogerCartSkip(BaseModel):
    name: str
    reason: str


class KrogerCartPushResult(BaseModel):
    added: list[KrogerCartLine]
    skipped: list[KrogerCartSkip]
    failed: list[KrogerCartSkip] = []  # matched but Kroger refused the UPC
    cart_url: str


class InstacartPushResult(BaseModel):
    url: str
    count: int


class ExportResultResponse(BaseModel):
    target: str
    format: str
    content_type: str
    body: str
    filename: str | None = None
