from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, UniqueConstraint, func
from app.core.db_types import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base


class BarcodeAlias(Base):
    __tablename__ = "barcode_aliases"
    __table_args__ = (UniqueConstraint("barcode", name="uq_barcode_aliases_barcode"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    barcode: Mapped[str] = mapped_column(String(64), nullable=False)
    item_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("items.id", ondelete="CASCADE"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    item = relationship("Item", back_populates="barcode_aliases")
