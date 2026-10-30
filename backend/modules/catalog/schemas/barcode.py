from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field

from modules.inventory.schemas.inventory import ScanLocationOption


class ShelfLifeHintRead(BaseModel):
    """A learned best-by offset plus the evidence behind it.

    ``observations`` and ``spread_days`` are not decoration: a prefilled date
    with no visible basis is a mystery number the operator can only accept or
    fight. These let the UI say *why* it thinks 14 days.
    """

    days: int = Field(description="Days from check-in to best-by.")
    observations: int = Field(description="How many dated purchases this is drawn from.")
    spread_days: int = Field(description="Range between the longest and shortest observation.")
    source: str = Field(default="learned", description="How the figure was derived.")


class BarcodeLookupResponse(BaseModel):
    barcode: str
    found: bool
    item_id: UUID | None = None
    item_name: str | None = None
    source: str | None = None
    item: dict | None = None  # Full item data if matched locally
    product: dict | None = None  # External provider data if found externally


class BarcodeScanRequest(BaseModel):
    """Request body for POST /barcodes/scan."""
    barcode: str = Field(min_length=1, max_length=64)
    auto_create: bool = True  # Auto-create Item if found externally but not in local DB


class BarcodeScanResponse(BaseModel):
    """Response for POST /barcodes/scan — includes full item + product data."""
    barcode: str
    found: bool
    created: bool = False  # True if a new Item was auto-created
    item_id: UUID | None = None
    item_name: str | None = None
    source: str | None = None
    item: dict | None = None
    product: dict | None = None
    # Where this item should be checked in (mirrors the scan-station's own
    # location resolution): one entry means "it already lives there, use it
    # with no prompt"; several (or, for a never-stocked item, every location)
    # means the caller should ask. Empty only when item_id is None.
    location_candidates: list[ScanLocationOption] = Field(default_factory=list)
    # Learned best-by offset for this item, or None to leave the field blank.
    # Absent until the item has enough consistent dated purchases to stand
    # behind a number — see modules.catalog.services.shelf_life.
    shelf_life_hint: ShelfLifeHintRead | None = None


class BarcodeAliasCreate(BaseModel):
    barcode: str
    item_id: UUID


class BarcodeAliasRead(BaseModel):
    id: UUID
    barcode: str
    item_id: UUID
    created_at: datetime
