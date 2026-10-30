from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, String, Text, func
from app.core.db_types import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class IntegrationConnection(Base):
    """A store connection (ADR 0018 §3). One row per provider.

    ``mode="brokered"``: the broker holds the refresh token; this row caches
    only the short-lived access token (Fernet-encrypted) plus the broker's
    connection ref. Local BYO-credential connections keep their existing
    storage (kroger_cart's connector_config rows) — this table is the
    brokered path's home, and the eventual destination for both.
    """

    __tablename__ = "integration_connections"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    provider: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)
    mode: Mapped[str] = mapped_column(String(16), nullable=False, default="brokered")
    broker_ref: Mapped[str] = mapped_column(String(64), nullable=False)
    scopes: Mapped[str | None] = mapped_column(String(255), nullable=True)
    external_hint: Mapped[str | None] = mapped_column(String(255), nullable=True)
    access_token_ciphertext: Mapped[str | None] = mapped_column(Text(), nullable=True)
    access_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active")
    last_error: Mapped[str | None] = mapped_column(Text(), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
