from __future__ import annotations

from datetime import date
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel


class PurchaseConnectorRead(BaseModel):
    key: str
    label: str
    configured: bool
    # Whether the connector can be set up from the admin Integrations screen.
    configurable: bool = False


class PurchaseConnectorsResponse(BaseModel):
    connectors: list[PurchaseConnectorRead]


class NormalizedLineRead(BaseModel):
    name: str
    quantity: Decimal
    unit: str
    upc: str | None = None
    unit_price: Decimal | None = None


class NormalizedOrderRead(BaseModel):
    vendor: str
    purchased_on: date | None = None
    external_id: str | None = None
    lines: list[NormalizedLineRead]


class PurchaseConnectorPreviewResponse(BaseModel):
    connector: str
    orders: list[NormalizedOrderRead]


class PurchaseConnectorImportRequest(BaseModel):
    location_id: UUID


class PurchaseConnectorImportResponse(BaseModel):
    connector: str
    orders: int
    imported_orders: int
    skipped_orders: int
    imported_lines: int
    transaction_ids: list[str]
