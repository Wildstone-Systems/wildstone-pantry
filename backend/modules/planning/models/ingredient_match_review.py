"""Planning-owned human review associations, never pantry or recipe mutations."""

from datetime import datetime
from uuid import UUID as PythonUUID
from uuid import uuid4

from sqlalchemy import DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db_types import JSONB, UUID
from app.models.base import Base


class IngredientMatchReview(Base):
    __tablename__ = "ingredient_match_reviews"

    id: Mapped[PythonUUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    recipe_id: Mapped[PythonUUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("recipes.id", ondelete="CASCADE"), index=True
    )
    ingredient_id: Mapped[PythonUUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("recipe_ingredients.id", ondelete="CASCADE"), index=True
    )
    candidate_item_id: Mapped[PythonUUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("items.id", ondelete="CASCADE")
    )
    actor_id: Mapped[PythonUUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    revision: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(24), default="pending")
    token_hash: Mapped[str] = mapped_column(String(64))
    document: Mapped[dict] = mapped_column(JSONB)


class IngredientMatchScope(Base):
    """One CAS head per recipe ingredient; earlier human receipts stay immutable."""

    __tablename__ = "ingredient_match_scopes"
    ingredient_id: Mapped[PythonUUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("recipe_ingredients.id", ondelete="CASCADE"), primary_key=True
    )
    revision: Mapped[int] = mapped_column(Integer, default=0)
    current_review_id: Mapped[PythonUUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
