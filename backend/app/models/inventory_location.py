from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, String, Text, func
from app.core.db_types import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base


class InventoryLocation(Base):
    __tablename__ = "inventory_locations"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(120), unique=True, nullable=False)
    description: Mapped[str | None] = mapped_column(Text(), nullable=True)
    # Soft-delete: archived locations keep their inventory and history and can be
    # reactivated; they're hidden from default catalog/picker lists.
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default=func.true())
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    transactions = relationship("InventoryTransaction", back_populates="location")
    inventory_rows = relationship("Inventory", back_populates="location")
