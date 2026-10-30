from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import DateTime, Numeric, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db_types import UUID
from app.models.base import Base


class IngredientReference(Base):
    """Seeded, editable lookup of common-ingredient unit-conversion hints (ADR 0011 D2).

    Supplies ``density_g_per_ml`` (volume→mass) and ``grams_per_each`` (count→mass)
    for the unit-conversion service when an ``items`` row hasn't set its own
    override. Ships with a small seed list and is CRUD-editable so the user — or a
    future agentic layer — can add or correct entries over time. This is a
    *starting point*, not authoritative: a missing/unknown ingredient simply means
    the conversion returns None (never a silent guess).
    """

    __tablename__ = "ingredient_reference"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    # Normalized (lower/stripped) ingredient name; the primary match key.
    name: Mapped[str] = mapped_column(String(200), nullable=False, unique=True)
    # Optional coarse category fallback (matched when no name row exists).
    category: Mapped[str | None] = mapped_column(String(100), nullable=True)
    density_g_per_ml: Mapped[Decimal | None] = mapped_column(Numeric(8, 4), nullable=True)
    grams_per_each: Mapped[Decimal | None] = mapped_column(Numeric(10, 2), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text(), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
