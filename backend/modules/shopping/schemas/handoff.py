from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, Field


class RetailerRead(BaseModel):
    """A retailer's declared vs currently-usable capabilities (ADR 0019 D1).
    The UI renders capabilities and greys out capabilities - available."""

    key: str
    label: str
    capabilities: list[str]
    available: list[str]
    status: str  # verified | unverified | broken (search-template health)


class RetailersResponse(BaseModel):
    retailers: list[RetailerRead]


class HandoffStartRequest(BaseModel):
    retailer: str = Field(min_length=1, max_length=40)


class HandoffItemUpdateRequest(BaseModel):
    state: str | None = None  # pending|opened|added|substituted|unavailable|skipped|purchased
    external_id: str | None = Field(default=None, max_length=120)
    external_name: str | None = Field(default=None, max_length=200)
    external_url: str | None = Field(default=None, max_length=500)
    note: str | None = None


class HandoffCompleteRequest(BaseModel):
    purchased_item_ids: list[UUID] = []
    # When given, purchased items are also added to inventory at this location
    # (same intake semantics as a CSV/receipt import). Omit to only record the
    # purchase and close out the list entries.
    location_id: UUID | None = None
    # Per-item override: handoff item id -> inventory location id. Beef
    # patties go in the fridge, kidney beans in the pantry — a single
    # trip-level location doesn't fit every line. Falls back to location_id
    # for any item not present here (old payloads behave exactly as today).
    item_locations: dict[UUID, UUID] = {}


class HandoffItemRead(BaseModel):
    id: UUID
    shopping_list_item_id: UUID | None
    item_id: UUID | None
    name: str
    quantity: Decimal
    unit: str
    state: str
    external_id: str | None
    external_name: str | None
    external_url: str | None
    note: str | None
    search_url: str | None
    search_query: str
    suggested_location_id: UUID | None = None


class HandoffSessionRead(BaseModel):
    id: UUID
    retailer_key: str
    retailer_label: str
    status: str
    started_at: datetime
    completed_at: datetime | None
    items: list[HandoffItemRead]
    purchase_id: UUID | None = None
    purchased_count: int | None = None


class HandoffSessionsResponse(BaseModel):
    sessions: list[HandoffSessionRead]
