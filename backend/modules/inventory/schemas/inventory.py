from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


class InventoryAddRequest(BaseModel):
    item_id: UUID
    location_id: UUID
    quantity: Decimal = Field(gt=0)
    unit: str = Field(default="each", min_length=1, max_length=24)
    expiration_date: date | None = None
    reason: str | None = "manual_add"
    note: str | None = None
    container_id: UUID | None = None
    # Container intake (ADR 0014): send how many identical containers + one
    # container's size. When both are set the server records a batch of that many
    # containers (quantity/unit are derived as sealed_count × container_size), so
    # the lot knows its per-container size. Omit for the legacy quantity/unit form.
    sealed_count: int | None = Field(default=None, ge=1)
    container_size: Decimal | None = Field(default=None, gt=0)
    container_unit: str | None = Field(default=None, min_length=1, max_length=24)
    unit_price: Decimal | None = Field(default=None, ge=0)


class InventoryRemoveRequest(BaseModel):
    item_id: UUID
    location_id: UUID
    quantity: Decimal = Field(gt=0)
    unit: str = Field(default="each", min_length=1, max_length=24)
    reason: str | None = "used"
    note: str | None = None


class InventoryAdjustRequest(BaseModel):
    item_id: UUID
    location_id: UUID
    quantity: Decimal = Field(ge=0)
    unit: str = Field(default="each", min_length=1, max_length=24)
    expiration_date: date | None = None
    reason: str | None = "adjustment"
    note: str | None = None


class InventoryMoveRequest(BaseModel):
    item_id: UUID
    source_location_id: UUID
    target_location_id: UUID
    quantity: Decimal = Field(gt=0)
    unit: str = Field(default="each", min_length=1, max_length=24)
    reason: str | None = "moved"
    note: str | None = None


class InventoryLineRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    inventory_id: UUID
    item_id: UUID
    item_name: str
    location_id: UUID
    quantity: Decimal
    unit: str
    expiration_date: date | None
    updated_at: datetime


class InventoryByLocationResponse(BaseModel):
    location_id: UUID
    location_name: str
    items: list[InventoryLineRead]


class InventoryMutationResponse(BaseModel):
    transaction_ids: list[UUID]
    inventory: list[InventoryLineRead]
    # The amount this mutation added/removed, in the item's base unit (ADR 0016) —
    # the delta, not the new on-hand total. Lets the client show "Added 2 × 0.5 gal
    # (3.79 L)" without re-deriving the conversion. None for multi-line mutations.
    added_quantity: Decimal | None = None
    added_unit: str | None = None


class TransactionRead(BaseModel):
    transaction_id: UUID
    item_id: UUID
    item_name: str
    location_id: UUID
    location_name: str
    quantity_change: Decimal
    unit: str
    transaction_type: str
    reason: str | None
    note: str | None
    created_at: datetime


class TransactionListResponse(BaseModel):
    transactions: list[TransactionRead]


class TransactionReverseResponse(BaseModel):
    """The new compensating entry that undoes a Log-tab mistake (ADR 0002) —
    both the original and the reversal stay in the log."""

    transaction_id: UUID
    reversed_id: UUID


class InventoryEventRead(BaseModel):
    """One immutable fact from the inventory event log (ADR 0002)."""

    event_id: UUID
    seq: int
    event_type: str
    item_id: UUID
    item_name: str
    location_id: UUID | None
    # Null for an off-pantry event (a meal logged with nothing deducted) or a
    # location that has since been deleted.
    location_name: str | None = None
    target_location_id: UUID | None
    quantity: Decimal
    unit: str
    expiration_date: date | None
    reason: str | None
    note: str | None
    actor: str | None
    source: str
    occurred_at: datetime


class InventoryEventListResponse(BaseModel):
    events: list[InventoryEventRead]


class InventoryScanRequest(BaseModel):
    # Exactly one of these identifies the item: a scanned/typed barcode, or an
    # item picked directly from the name-search dropdown (no scanner handy).
    barcode: str | None = Field(default=None, min_length=1, max_length=64)
    item_id: UUID | None = None
    # Optional: when omitted the station resolves the location from stock — a lone
    # location is used automatically, several prompts for a choice (scan feedback).
    location_id: UUID | None = None
    # Answer to a ``needs_container`` prompt: consume from THIS container. Only
    # meaningful on ``out``; ignored on ``in``.
    lot_id: UUID | None = None
    direction: str = Field(pattern="^(in|out)$")  # in = add, out = consume
    quantity: Decimal = Field(default=Decimal("1"), gt=0)
    unit: str = Field(default="each", min_length=1, max_length=24)
    # Best-by for an add (in); ignored on consume. Lets perishables like milk get
    # dated right at the scan station instead of a separate edit (scan feedback).
    expiration_date: date | None = None

    @model_validator(mode="after")
    def _requires_barcode_or_item(self) -> "InventoryScanRequest":
        if not self.barcode and not self.item_id:
            raise ValueError("Provide either barcode or item_id")
        return self


class ScanLocationOption(BaseModel):
    location_id: UUID
    location_name: str
    quantity: str  # current on-hand there (0 for a fresh add target)


class ScanContainerOption(BaseModel):
    """One open container to choose between when scanning an empty one out."""

    lot_id: UUID
    quantity_remaining: Decimal
    unit: str
    expiration_date: date | None = None
    status: str = "open"


class InventoryScanResponse(BaseModel):
    # "done" = the move happened; "needs_location" = ambiguous, pick from
    # ``candidate_locations`` and re-scan with that location;
    # "needs_container" = several containers of this item are open and only the
    # person holding the empty one knows which it was — pick from
    # ``candidate_containers`` and re-scan with that ``lot_id`` (ADR 0023 D3).
    status: str = "done"
    direction: str
    item_id: UUID
    item_name: str
    inventory: list[InventoryLineRead] = Field(default_factory=list)
    resolved_location_id: UUID | None = None
    resolved_location_name: str | None = None
    candidate_locations: list[ScanLocationOption] = Field(default_factory=list)
    candidate_containers: list[ScanContainerOption] = Field(default_factory=list)
    # The handle for undoing this scan. None when nothing was written (a prompt,
    # or an already-empty container).
    transaction_id: UUID | None = None


class SetExpirationRequest(BaseModel):
    """Set the best-by date for an item's on-hand stock at a location."""

    item_id: UUID
    location_id: UUID
    expiration_date: date


class SetExpirationResponse(BaseModel):
    relabeled: int


class LotRelabelRequest(BaseModel):
    """Set one lot's best-by date (does not touch other lots)."""

    expiration_date: date


class OpenContainerRequest(BaseModel):
    """Open one sealed container of a batch lot (ADR 0014)."""

    lot_id: UUID


class DiscardContainerRequest(BaseModel):
    """Discard/spoil a specific container: an open one, or one sealed unit of a
    batch. ``quantity`` overrides the default (a full container's content)."""

    lot_id: UUID
    quantity: Decimal | None = Field(default=None, gt=0)
    reason: str | None = "spoiled"
    note: str | None = None


class LotRead(BaseModel):
    lot_id: UUID
    item_id: UUID
    item_name: str
    location_id: UUID
    container_id: UUID | None = None
    container_name: str | None = None
    status: str  # sealed | open | depleted
    quantity_remaining: Decimal
    unit: str
    container_size: Decimal | None = None
    container_unit: str | None = None
    sealed_count: int | None = None
    expiration_date: date | None = None
    unit_price: Decimal | None = None
    frozen: bool
    days_until_expiration: int | None = None


class LotListResponse(BaseModel):
    lots: list[LotRead]


class WorklistRow(BaseModel):
    """One thing worth acting on. ``source`` says why it surfaced — "expiring"
    (has a deadline), "open" (a started container), "recent" (ordinary usage) —
    so the UI can label the row honestly instead of implying every row is
    urgent. ``lot_id`` is None only on a "recent" row, which names an item
    rather than a specific container."""

    source: str
    item_id: UUID
    item_name: str
    location_id: UUID
    lot_id: UUID | None = None
    quantity_remaining: Decimal
    unit: str | None = None
    expiration_date: date | None = None
    days_until_expiration: int | None = None
    status: str | None = None
    sealed_count: int | None = None
    container_size: Decimal | None = None


class WorklistResponse(BaseModel):
    location_id: UUID
    rows: list[WorklistRow]


class LotConsumeRequest(BaseModel):
    """A partial draw from ONE named container. Omit ``quantity`` (or use the
    /finish route) to take the whole open container / one sealed unit."""

    quantity: Decimal | None = Field(default=None, gt=0)
    note: str | None = None


class LotActionResponse(BaseModel):
    """Result of a lot-targeted action, with what's needed to undo it.

    ``already_resolved`` marks the no-op case — the container was empty before
    this call (someone else got there first, or a double-tap). Not an error: the
    intent is satisfied, there is just nothing to record and nothing to undo.
    """

    lots: list[LotRead]
    transaction_id: UUID | None = None
    quantity: Decimal = Decimal("0")
    already_resolved: bool = False


class ContainerCreate(BaseModel):
    location_id: UUID
    name: str = Field(min_length=1, max_length=120)
    description: str | None = None


class ContainerRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    location_id: UUID
    name: str
    description: str | None
    created_at: datetime


class ContainerListResponse(BaseModel):
    containers: list[ContainerRead]
