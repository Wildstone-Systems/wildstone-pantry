"""Inventory read-model projections (derived, rebuildable) — ADR 0002.

These tables are folded from ``inventory_events`` and can always be regenerated
by replaying the log in ``seq`` order. ``last_applied_seq`` records how far each
row has been advanced so drift is detectable.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db_types import UUID
from app.models.base import Base


class InventoryBalance(Base):
    """Current quantity per (item, location) — derived from the event log."""

    __tablename__ = "inv_balance"
    __table_args__ = (UniqueConstraint("item_id", "location_id", name="uq_inv_balance_item_location"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    item_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("items.id", ondelete="CASCADE"), nullable=False
    )
    location_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("inventory_locations.id", ondelete="CASCADE"), nullable=False
    )
    quantity: Mapped[Decimal] = mapped_column(Numeric(12, 3), nullable=False, default=0)
    unit: Mapped[str] = mapped_column(String(24), nullable=False, default="each")
    last_applied_seq: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class InventoryLot(Base):
    """A distinct intake batch with its own expiry/price — derived from events."""

    __tablename__ = "inv_lot"

    # Lot identity equals the originating event's lot_id.
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    item_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("items.id", ondelete="CASCADE"), nullable=False
    )
    location_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("inventory_locations.id", ondelete="CASCADE"), nullable=False
    )
    quantity_remaining: Mapped[Decimal] = mapped_column(Numeric(12, 3), nullable=False, default=0)
    unit: Mapped[str] = mapped_column(String(24), nullable=False, default="each")
    expiration_date: Mapped[date | None] = mapped_column(Date(), nullable=True)
    unit_price: Mapped[Decimal | None] = mapped_column(Numeric(12, 4), nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="sealed")  # sealed | open | depleted
    frozen: Mapped[bool] = mapped_column(Boolean(), nullable=False, default=False)
    # Container model (ADR 0014). A sealed-batch row holds ``sealed_count``
    # containers of ``container_size``; an open-container row (status="open") has
    # its partial content in ``quantity_remaining``. Null on legacy/pre-0014 lots.
    container_size: Mapped[Decimal | None] = mapped_column(Numeric(12, 3), nullable=True)
    container_unit: Mapped[str | None] = mapped_column(String(24), nullable=True)
    sealed_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Derived container placement (GH #9). Rebuildable projection, so no FK.
    container_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    source_event_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    last_applied_seq: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)


class InventoryProjectionState(Base):
    """Bookkeeping: how far each named projection has consumed the event log."""

    __tablename__ = "inv_projection_state"

    name: Mapped[str] = mapped_column(String(64), primary_key=True)
    last_applied_seq: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
