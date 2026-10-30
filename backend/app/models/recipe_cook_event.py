from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import DateTime, ForeignKey, Integer, Numeric, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db_types import JSONB, UUID
from app.models.base import Base


class RecipeCookEvent(Base):
    __tablename__ = "recipe_cook_events"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    recipe_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("recipes.id", ondelete="CASCADE"), nullable=False)
    location_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("inventory_locations.id", ondelete="CASCADE"), nullable=False)
    servings: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    leftover_servings: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    note: Mapped[str | None] = mapped_column(Text(), nullable=True)
    # What was substituted this cook (step 5 / ADR 0011 D5): a list of
    # {original_item_id, substitute_item_id, ratio} so history shows the real swap.
    substitutions: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    # Absolute servings produced, when cooked via the cook-along path (ADR 0022
    # §6). Null for a legacy batch-multiplier cook, where `servings` above is
    # the multiplier rather than a serving count.
    servings_made: Mapped[Decimal | None] = mapped_column(Numeric(12, 3), nullable=True)
    # Per-line amount changes/omissions applied to THIS cook: a list of
    # {ingredient_id, quantity, unit, omit}. Null when nothing was overridden.
    # History has to show you left out the cilantro — a cook record that claims
    # the full ingredient list is a lie the consumption trends inherit.
    ingredient_overrides: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    recipe = relationship("Recipe", back_populates="cook_events")
    location = relationship("InventoryLocation")
