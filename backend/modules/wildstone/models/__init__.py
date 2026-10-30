"""Tables owned by the Wildstone integration module (ADR 0026)."""

from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy import BigInteger, Boolean, Date, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db_types import JSONB, UUID
from app.models.base import Base


class WildstoneOutbox(Base):
    """Household event envelopes waiting for (or done with) delivery to Keystone.

    Written in the same transaction that advances the relay cursor, so an event
    is either in here or its source row has not been read yet — never lost in
    between. ``event_id`` is deterministic and unique: re-deriving an event is a
    no-op here, and Keystone de-duplicates on it too.
    """

    __tablename__ = "wildstone_outbox"

    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True)
    event_id: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    envelope: Mapped[dict] = mapped_column(JSONB, nullable=False)
    # pending | delivered | rejected (Keystone refused it; retrying won't help)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text(), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class WildstoneRelayState(Base):
    """Where the relay has read up to. One row (``name='default'``)."""

    __tablename__ = "wildstone_relay_state"

    name: Mapped[str] = mapped_column(String(32), primary_key=True)
    ledger_seq: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    # Where the ledger stood when the relay began: the floor for re-reading
    # rows that committed late. Older history is never news.
    start_seq: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0, server_default="0")
    # The grocery list's fingerprint as last reported: a different one, once
    # the edits settle, is a pantry.shopping_list_changed.
    list_fingerprint: Mapped[str | None] = mapped_column(String(64), nullable=True)
    cook_cursor: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    expiry_scan_on: Mapped[date | None] = mapped_column(Date(), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class WildstoneStockFlag(Base):
    """Whether an item was below its restock threshold at the last relay pass,
    so ``pantry.stock_low`` fires on the crossing, not on every pass."""

    __tablename__ = "wildstone_stock_flags"

    item_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    low: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # Bumped on each new crossing so its event id differs from the last one.
    crossings: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class WildstoneMemberLink(Base):
    """Which Wildstone member a household profile is (ADR-W024 A8)."""

    __tablename__ = "wildstone_member_links"

    profile_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("household_profiles.id", ondelete="CASCADE"), primary_key=True
    )
    member: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


__all__ = ["WildstoneOutbox", "WildstoneRelayState", "WildstoneStockFlag", "WildstoneMemberLink"]
