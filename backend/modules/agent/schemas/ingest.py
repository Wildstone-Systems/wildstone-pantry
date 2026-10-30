from __future__ import annotations

from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, Field


class ProductUrlIngestRequest(BaseModel):
    url: str = Field(min_length=1)
    add_to_shopping: bool = True
    add_to_inventory: bool = False
    location_id: UUID | None = None
    container_id: UUID | None = None
    quantity: Decimal = Field(default=Decimal("1"), gt=0)
    unit: str = Field(default="each", min_length=1, max_length=24)


class ProductUrlIngestResponse(BaseModel):
    item_id: UUID
    item_name: str
    barcode: str | None
    image_url: str | None
    shopping_item_id: UUID | None
    inventory_added: bool
    extracted: dict
