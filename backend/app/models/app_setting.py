from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class AppSetting(Base):
    """Small instance-level key/value store (ADR 0018 §7).

    Holds first-run wizard state (``setup_completed``, ``household_name``,
    ``lan_open_ack``) and runtime toggles that must survive restarts without
    an env edit (``auth_enabled_override``). Not for secrets — those belong in
    the encrypted ``connector_config`` store (ADR 0009).
    """

    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(Text(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
