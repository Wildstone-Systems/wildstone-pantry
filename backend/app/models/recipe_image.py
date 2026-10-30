"""Uploaded recipe pictures.

Kept in its own table rather than as columns on ``recipes`` for one reason: the
blob would otherwise be dragged into every recipe list query. A separate table
makes "load the picture" an explicit lookup nobody does by accident, and the
one-row-per-recipe primary key enforces "a recipe has at most one picture" in
the schema instead of in application code.

Bytes live in the database, not on a volume. For a single-host household
install that is the safer default — the picture is covered by whatever backs up
Postgres rather than quietly living in an unbacked-up directory, and there is no
static-file mount to configure or get wrong. Uploads are re-encoded and bounded
before they land here (see ``modules/planning/services/recipe_images.py``), so
the table's size is a function of how many recipes exist, not of what came off
someone's phone camera.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, LargeBinary, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db_types import UUID
from app.models.base import Base


class RecipeImage(Base):
    __tablename__ = "recipe_images"

    # The recipe id *is* the key: one picture per recipe, enforced by the schema.
    recipe_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("recipes.id", ondelete="CASCADE"), primary_key=True
    )
    content_type: Mapped[str] = mapped_column(String(64), nullable=False)
    data: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    width: Mapped[int | None] = mapped_column(Integer, nullable=True)
    height: Mapped[int | None] = mapped_column(Integer, nullable=True)
    byte_size: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
