from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy import Date, DateTime, ForeignKey, Integer, String, func
from app.core.db_types import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base


class Purchase(Base):
    __tablename__ = "purchases"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    merchant: Mapped[str | None] = mapped_column(String(120), nullable=True)
    source: Mapped[str] = mapped_column(String(40), nullable=False, default="csv")
    purchased_on: Mapped[date | None] = mapped_column(Date(), nullable=True)
    location_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("inventory_locations.id", ondelete="SET NULL"), nullable=True
    )
    status: Mapped[str] = mapped_column(String(40), nullable=False, default="pending")
    source_fingerprint: Mapped[str | None] = mapped_column(String(64), nullable=True)
    line_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    line_items = relationship("PurchaseLineItem", back_populates="purchase", cascade="all,delete")
