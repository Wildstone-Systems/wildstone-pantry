from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, Integer, String, Text, func
from app.core.db_types import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class EntitlementState(Base):
    """The instance's activation record (ADR 0018 §1–§2). At most one row.

    Holds the signed entitlement token plus the instance's Ed25519 private key
    (Fernet-encrypted — same custody as connector credentials, ADR 0009).
    ``resolve_entitlement()`` reads this row and verifies the token offline;
    the background refresher renews it daily.
    """

    __tablename__ = "entitlement_state"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    instance_id: Mapped[str] = mapped_column(String(64), nullable=False)
    key_hint: Mapped[str] = mapped_column(String(8), nullable=False)  # last 4 of the activation key
    tier: Mapped[str] = mapped_column(String(32), nullable=False)
    token: Mapped[str] = mapped_column(Text(), nullable=False)
    private_key_ciphertext: Mapped[str] = mapped_column(Text(), nullable=False)
    server_url: Mapped[str] = mapped_column(String(255), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_refresh_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    refresh_failures: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
