from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import DateTime, ForeignKey, Numeric, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db_types import JSONB, UUID
from app.models.base import Base


class ShoppingListItem(Base):
    """An entry on the household shopping (buy) list — step 3.

    ``source="auto"`` entries are reconciled from catalog restock thresholds
    (added when on-hand drops below the threshold, removed when it recovers).
    ``source="manual"`` entries are added by hand and are never auto-removed.
    ``status`` moves ``pending`` -> ``purchased``. ``item_id`` links to the
    catalog when known; ``name`` is denormalized so free-text entries work too.
    """

    __tablename__ = "shopping_list_items"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    item_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("items.id", ondelete="CASCADE"), nullable=True
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    quantity: Mapped[Decimal] = mapped_column(Numeric(12, 3), nullable=False, default=1)
    unit: Mapped[str] = mapped_column(String(24), nullable=False, default="each")
    source: Mapped[str] = mapped_column(String(16), nullable=False, default="manual")  # auto | manual
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")  # pending | purchased
    note: Mapped[str | None] = mapped_column(Text(), nullable=True)
    # Snapshot of the chosen vendor/product offer (ADR 0013). Price is captured
    # at selection time, never live. None until the entry is resolved to an offer.
    chosen_offer: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
