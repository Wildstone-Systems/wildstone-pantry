from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import Boolean, DateTime, ForeignKey, Numeric, String, func
from app.core.db_types import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base


class RecipeIngredient(Base):
    __tablename__ = "recipe_ingredients"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    recipe_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("recipes.id", ondelete="CASCADE"), nullable=False)
    item_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("items.id", ondelete="CASCADE"), nullable=False)
    quantity_required: Mapped[Decimal] = mapped_column(Numeric(12, 3), nullable=False)
    unit: Mapped[str] = mapped_column(String(24), nullable=False, default="each")
    # ADR 0025 §5. 'amount' — the usual thing: `quantity_required unit` of the
    # item. 'package' — N WHOLE packages, whatever they happen to weigh, resolved
    # against the lots actually on the shelf. The second is the only honest way
    # to write "one pork butt roast" when every roast weighs something different:
    # an amount line has to name a number, and any number it names is wrong for
    # every package except one.
    quantity_mode: Mapped[str] = mapped_column(
        String(16), nullable=False, default="amount", server_default="amount"
    )
    # ADR 0017 §4.1: a "to taste"/garnish line — informational, excluded from the
    # can-make score and never auto-deducted on cook.
    optional: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=func.false(), default=False)
    # ADR 0025 §3: the line a 'yield_ratio' output is computed from — the meat in
    # a pulled-pork recipe, so rub and sauce don't inflate the yield. Explicit
    # rather than inferred: the ratio has to stay meaningful when a batch gets
    # more sauce, an ingredient is substituted, or the recipe is scaled, and
    # "whichever input weighs most" picks the liquid in a braise without saying so.
    yield_basis: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=func.false(), default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    recipe = relationship("Recipe", back_populates="ingredients")
    item = relationship("Item")
