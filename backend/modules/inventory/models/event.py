"""Inventory event store (write model) — ADR 0002.

Append-only log of inventory facts. ``seq`` is the global monotonic order used
for deterministic projection replay; ``event_id`` is the stable business id.
Rows are never updated or deleted — corrections are new compensating events.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import BigInteger, Date, DateTime, ForeignKey, Integer, Numeric, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db_types import JSONB, UUID
from app.models.base import Base

# Event vocabulary (past-tense facts). Keep in sync with the projection folds.
EVENT_TYPES = (
    "purchased",
    "produced",
    "consumed",
    "opened",
    "transferred",
    "spoiled",
    "adjusted",
    "frozen",
    "thawed",
    "relabeled",
    "reconciled",
)


class InventoryEvent(Base):
    __tablename__ = "inventory_events"

    # Global insertion order; the sole ordering key for replay. BIGSERIAL on
    # PostgreSQL; INTEGER PRIMARY KEY (rowid alias, autoincrements) on SQLite.
    seq: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True
    )
    event_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, unique=True, default=uuid.uuid4)
    event_type: Mapped[str] = mapped_column(String(32), nullable=False)

    item_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("items.id", ondelete="CASCADE"), nullable=False
    )
    location_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("inventory_locations.id", ondelete="SET NULL"), nullable=True
    )
    target_location_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("inventory_locations.id", ondelete="SET NULL"), nullable=True
    )
    # Logical lot this event affects/creates. Lots are a projection, so this is
    # not a FK (the lot table can be rebuilt from the log).
    lot_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    # Optional container placement recorded at intake (GH #9). No FK: the log is
    # immutable, so deleting a container must never mutate history.
    container_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)

    quantity: Mapped[Decimal] = mapped_column(Numeric(12, 3), nullable=False, default=0)
    unit: Mapped[str] = mapped_column(String(24), nullable=False, default="each")
    unit_price: Mapped[Decimal | None] = mapped_column(Numeric(12, 4), nullable=True)
    expiration_date: Mapped[date | None] = mapped_column(Date(), nullable=True)

    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    actor: Mapped[str | None] = mapped_column(String(64), nullable=True)
    source: Mapped[str] = mapped_column(String(32), nullable=False, default="api")
    correlation_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)

    reason: Mapped[str | None] = mapped_column(String(120), nullable=True)
    note: Mapped[str | None] = mapped_column(Text(), nullable=True)
    payload: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
