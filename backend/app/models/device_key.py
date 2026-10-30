from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, func
from app.core.db_types import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class DeviceKey(Base):
    """A phone/tablet credential (ADR 0018 §8), modeled on agent keys but
    bound to a user: ``dk_`` prefix, SHA-256 hashed at rest, revocable, no
    expiry — possession of the device is the session. Minted only through the
    QR/one-time-code pairing flow.
    """

    __tablename__ = "device_keys"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    key_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
