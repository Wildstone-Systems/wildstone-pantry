"""Planning-owned explicit household identity review receipts."""

from datetime import datetime
from uuid import UUID as PythonUUID

from sqlalchemy import DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db_types import JSONB, UUID
from app.models.base import Base


class RankedIngredientReview(Base):
    __tablename__ = "ranked_ingredient_reviews"
    id: Mapped[PythonUUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    recipe_id: Mapped[PythonUUID] = mapped_column(UUID(as_uuid=True), ForeignKey("recipes.id", ondelete="CASCADE"))
    ingredient_id: Mapped[PythonUUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("recipe_ingredients.id", ondelete="CASCADE")
    )
    actor_id: Mapped[PythonUUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    revision: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(24))
    token_hash: Mapped[str] = mapped_column(String(64))
    document: Mapped[dict] = mapped_column(JSONB)
