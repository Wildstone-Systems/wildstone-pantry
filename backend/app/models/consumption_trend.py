from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import DateTime, ForeignKey, Numeric, func
from app.core.db_types import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class ConsumptionTrend(Base):
    __tablename__ = "consumption_trends"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    item_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("items.id", ondelete="CASCADE"), nullable=False)
    location_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("inventory_locations.id", ondelete="CASCADE"), nullable=False)
    avg_daily_usage: Mapped[Decimal] = mapped_column(Numeric(12, 3), nullable=False)
    days_to_depletion: Mapped[Decimal | None] = mapped_column(Numeric(12, 3), nullable=True)
    sampled_days: Mapped[int] = mapped_column(nullable=False, default=14)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
