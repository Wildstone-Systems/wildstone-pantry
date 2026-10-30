from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import DateTime, ForeignKey, Numeric, String, Text, func
from app.core.db_types import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base


class PurchaseLineItem(Base):
    __tablename__ = "purchase_line_items"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    purchase_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("purchases.id", ondelete="CASCADE"), nullable=False
    )
    raw_line: Mapped[str] = mapped_column(Text(), nullable=False)
    parsed_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    parsed_quantity: Mapped[Decimal] = mapped_column(Numeric(12, 3), nullable=False, default=1)
    parsed_unit: Mapped[str] = mapped_column(String(24), nullable=False, default="each")
    parsed_barcode: Mapped[str | None] = mapped_column(String(64), nullable=True)
    matched_item_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("items.id", ondelete="SET NULL"), nullable=True
    )
    expiration_days_estimate: Mapped[int | None] = mapped_column(nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    purchase = relationship("Purchase", back_populates="line_items")
    matched_item = relationship("Item", back_populates="purchase_lines")

