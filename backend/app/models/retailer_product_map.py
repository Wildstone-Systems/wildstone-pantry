from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db_types import UUID
from app.models.base import Base


class RetailerProductMap(Base):
    """Cross-trip memory: "item X at retailer R is product P" (ADR 0019 D4).

    ``chosen_offer`` on a shopping entry stays the per-trip snapshot; this
    table is the compounding asset every confirmed pick feeds. Consumers pick
    the highest ``confirmed_count`` (most recent as tiebreak).
    """

    __tablename__ = "retailer_product_maps"
    __table_args__ = (UniqueConstraint("item_id", "retailer_key", "external_id", name="uq_retailer_map_identity"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    item_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("items.id", ondelete="CASCADE"), nullable=False
    )
    retailer_key: Mapped[str] = mapped_column(String(40), nullable=False)
    external_id: Mapped[str] = mapped_column(String(120), nullable=False)
    external_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    external_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    upc: Mapped[str | None] = mapped_column(String(32), nullable=True)
    size: Mapped[str | None] = mapped_column(String(80), nullable=True)
    source: Mapped[str] = mapped_column(String(16), nullable=False)  # handoff | cart_push | offer_pick | manual
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)
    confirmed_count: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    last_confirmed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
