from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field


class ResolveItemLine(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    barcode: str | None = Field(default=None, max_length=64)
    brand: str | None = Field(default=None, max_length=120)


class ResolveItemsRequest(BaseModel):
    lines: list[ResolveItemLine] = Field(min_length=1, max_length=100)
    # Default is a dry run: report matches, create nothing.
    create_missing: bool = False


class ResolvedItem(BaseModel):
    name: str
    barcode: str | None
    match: Literal["barcode", "existing", "created", "not_found"]
    item_id: UUID | None
    item_name: str | None
    suggested_expiry_days: int


class ResolveItemsResponse(BaseModel):
    items: list[ResolvedItem]
    created_count: int
    not_found_count: int
