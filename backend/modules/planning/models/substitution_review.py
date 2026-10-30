"""Explicit human substitution choices; never model-authorized cooking."""

from datetime import datetime
from decimal import Decimal
from uuid import UUID as PyUUID
from uuid import uuid4

from sqlalchemy import DateTime, ForeignKey, Numeric, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db_types import JSONB, UUID
from app.models.base import Base


class SubstitutionReview(Base):
    __tablename__ = "substitution_reviews"
    id: Mapped[PyUUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    session_id: Mapped[PyUUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("cook_sessions.id", ondelete="CASCADE"), index=True
    )
    recipe_id: Mapped[PyUUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("recipes.id", ondelete="CASCADE"), index=True
    )
    ingredient_id: Mapped[PyUUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("recipe_ingredients.id", ondelete="CASCADE"), index=True
    )
    from_item_id: Mapped[PyUUID] = mapped_column(UUID(as_uuid=True), ForeignKey("items.id", ondelete="CASCADE"))
    to_item_id: Mapped[PyUUID] = mapped_column(UUID(as_uuid=True), ForeignKey("items.id", ondelete="CASCADE"))
    context: Mapped[dict] = mapped_column(JSONB, nullable=False)
    snapshot: Mapped[dict] = mapped_column(JSONB, nullable=False)
    binding: Mapped[str] = mapped_column(String(64), nullable=False)
    recipe_binding: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    scope: Mapped[str | None] = mapped_column(String(16), nullable=True)
    ratio: Mapped[Decimal | None] = mapped_column(Numeric(10, 4), nullable=True)
    from_unit: Mapped[str] = mapped_column(String(24), nullable=False)
    unit: Mapped[str | None] = mapped_column(String(24), nullable=True)
    evaluation: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    actor_id: Mapped[str] = mapped_column(String(64), nullable=False)
    approved_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
