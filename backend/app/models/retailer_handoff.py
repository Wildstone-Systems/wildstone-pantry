from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import DateTime, ForeignKey, Numeric, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db_types import UUID
from app.models.base import Base


class RetailerHandoffSession(Base):
    """A guided search-handoff shopping session (ADR 0019 D3).

    Not a pretend cart: PantryOS-side state only, nothing round-trips to the
    retailer. Sessions are rows, so they survive restarts and can be resumed.
    """

    __tablename__ = "retailer_handoff_sessions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    retailer_key: Mapped[str] = mapped_column(String(40), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active")  # active | completed | abandoned
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    items = relationship(
        "RetailerHandoffItem", back_populates="session", cascade="all,delete", order_by="RetailerHandoffItem.name_snapshot"
    )


class RetailerHandoffItem(Base):
    """One shopping-list entry snapshotted into a handoff session.

    The snapshot (name/quantity/unit/item_id) is deliberate: the live shopping
    list keeps moving while the user shops, and completion must still know what
    was asked for. State is a vocabulary, not a straitjacket — any transition
    is allowed (all are user-driven).
    """

    __tablename__ = "retailer_handoff_items"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("retailer_handoff_sessions.id", ondelete="CASCADE"), nullable=False
    )
    shopping_list_item_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("shopping_list_items.id", ondelete="SET NULL"), nullable=True
    )
    item_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("items.id", ondelete="SET NULL"), nullable=True
    )
    name_snapshot: Mapped[str] = mapped_column(String(200), nullable=False)
    quantity_snapshot: Mapped[Decimal] = mapped_column(Numeric(12, 3), nullable=False, default=1)
    unit_snapshot: Mapped[str] = mapped_column(String(24), nullable=False, default="each")
    # pending | opened | added | substituted | unavailable | skipped | purchased
    state: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    external_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    external_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    external_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    note: Mapped[str | None] = mapped_column(Text(), nullable=True)

    session = relationship("RetailerHandoffSession", back_populates="items")
