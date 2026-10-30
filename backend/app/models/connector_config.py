from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class ConnectorConfig(Base):
    """In-app configuration for a vendor connector (ADR 0009).

    One row per connector ``key`` (e.g. "imap"). ``ciphertext`` is a Fernet token
    over the full JSON config (including secrets); it is decrypted server-side to
    build effective config and is never returned to clients verbatim.
    """

    __tablename__ = "connector_config"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    ciphertext: Mapped[str] = mapped_column(Text(), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
