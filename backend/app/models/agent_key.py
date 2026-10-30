from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db_types import UUID
from app.models.base import Base


class AgentKey(Base):
    """A long-lived, revocable credential for an autonomous agent.

    Unlike short-lived ``AuthToken``s (browser sessions), an agent key does not
    expire; it is revoked explicitly. Only the SHA-256 hash of the secret is
    stored — the plaintext is shown once at creation. The key is owned by a user
    so agent actions inherit a stable identity for auditing.
    """

    __tablename__ = "agent_keys"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    # Non-secret display prefix (e.g. "pk_ab12") so keys are identifiable in a list.
    token_prefix: Mapped[str] = mapped_column(String(16), nullable=False)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    scopes: Mapped[str] = mapped_column(String(255), nullable=False, default="read,write")
    revoked: Mapped[bool] = mapped_column(Boolean(), nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
