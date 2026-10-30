from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db_types import UUID
from app.models.base import Base


class RetailerLink(Base):
    """A search-link retailer as pure data (ADR 0019 D1).

    A retailer whose only capability is a site-search URL needs no code: one
    row here (shipped as a seed, admin-editable, updatable without a release)
    makes it a working handoff target. ``status`` follows the template-table
    operating rules: 404/redirect-home = broken; a 403 from a bot wall means
    "unverified", never "broken" — the user's browser still gets through.
    """

    __tablename__ = "retailer_links"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    key: Mapped[str] = mapped_column(String(40), nullable=False, unique=True)
    label: Mapped[str] = mapped_column(String(80), nullable=False)
    domain: Mapped[str] = mapped_column(String(120), nullable=False)  # site-search fallback target
    search_template: Mapped[str | None] = mapped_column(String(300), nullable=True)  # "…{q}…"
    product_template: Mapped[str | None] = mapped_column(String(300), nullable=True)  # "…{id}…"
    encoder: Mapped[str] = mapped_column(String(10), nullable=False, default="plus")  # plus | percent
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="unverified")  # verified | unverified | broken
    last_verified: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text(), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
