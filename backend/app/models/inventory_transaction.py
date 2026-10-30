from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import DateTime, ForeignKey, Numeric, String, Text, func
from app.core.db_types import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base


class InventoryTransaction(Base):
    __tablename__ = "inventory_transactions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    item_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("items.id", ondelete="CASCADE"),
        nullable=False,
    )
    location_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("inventory_locations.id", ondelete="CASCADE"),
        nullable=False,
    )
    quantity_change: Mapped[Decimal] = mapped_column(Numeric(12, 3), nullable=False)
    unit: Mapped[str] = mapped_column(String(24), nullable=False, default="each")
    transaction_type: Mapped[str] = mapped_column(String(40), nullable=False, default="adjustment")
    reason: Mapped[str | None] = mapped_column(String(120), nullable=True)
    note: Mapped[str | None] = mapped_column(Text(), nullable=True)
    # Which container this acted on, when it acted on one (ADR 0023). Set by the
    # lot-targeted verbs (finish / consume-from-lot / discard); None for the
    # aggregate add/remove path. Reversal reads it to put the content back into
    # the SAME lot instead of re-guessing FEFO. No FK — `inv_lot` is a
    # projection, dropped and rebuilt wholesale on every replay.
    lot_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    item = relationship("Item", back_populates="transactions")
    location = relationship("InventoryLocation", back_populates="transactions")
