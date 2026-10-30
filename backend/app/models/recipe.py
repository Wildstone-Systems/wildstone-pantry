from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, Numeric, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db_types import JSONB, UUID
from app.models.base import Base


class Recipe(Base):
    __tablename__ = "recipes"
    __table_args__ = (
        UniqueConstraint("external_source", "external_id", name="uq_recipes_external_identity"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    instructions: Mapped[str | None] = mapped_column(Text(), nullable=True)
    # Base yield in servings (step 5 / ADR 0011 D3): the divisor for per-serving
    # nutrition and the default cook quantity. NOT a rescale of ingredient
    # decrements — cook's ``servings`` argument stays "servings to make".
    servings: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    source: Mapped[str | None] = mapped_column(String(32), nullable=True)
    source_filename: Mapped[str | None] = mapped_column(String(256), nullable=True)
    source_text: Mapped[str | None] = mapped_column(Text(), nullable=True)
    import_review: Mapped[dict | None] = mapped_column(JSONB(), nullable=True)

    # Stable external identity: which upstream catalogue this recipe came from and
    # its id there ("themealdb" / "52802"). Together they make re-import an UPDATE
    # instead of a duplicate — the pair is unique, so an importer that reruns a feed
    # converges on one row per upstream recipe rather than one per run. Both null for
    # hand-entered recipes, and NULLs don't collide under the unique constraint, so
    # any number of local recipes coexist.
    external_source: Mapped[str | None] = mapped_column(String(40), nullable=True)
    external_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    external_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    # Picture hosted upstream (TheMealDB's strMealThumb, or a URL someone pasted).
    # An *uploaded* picture lives in `recipe_images` instead and takes precedence —
    # see `resolve_image_src`, which is the single place that ordering is decided.
    image_url: Mapped[str | None] = mapped_column(String(500), nullable=True)

    # Output/yield (step 4a): when output_item_id is set, cooking produces
    # output_quantity * servings of that item as a new tracked lot (ADR 0003 D3).
    output_item_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("items.id", ondelete="SET NULL"), nullable=True
    )
    output_quantity: Mapped[Decimal | None] = mapped_column(Numeric(12, 3), nullable=True)
    output_unit: Mapped[str | None] = mapped_column(String(24), nullable=True)
    # How the yield is worked out (ADR 0025 §3). 'fixed' = output_quantity ×
    # servings, the original behaviour. 'yield_ratio' = a fraction of the mass
    # that actually went in, so a 4.87 lb roast and a 9 lb roast stop producing
    # the same number. A weighed dish (cook's `output_weight_grams`) beats both.
    output_mode: Mapped[str] = mapped_column(
        String(16), nullable=False, default="fixed", server_default="fixed"
    )
    # Output mass ÷ basis mass — pulled pork comes off the smoker at ~0.6 of the
    # raw weight. Only read when output_mode is 'yield_ratio'.
    output_yield_ratio: Mapped[Decimal | None] = mapped_column(Numeric(6, 4), nullable=True)

    # An in-progress ad hoc cook (ADR 0024 §1): the session is this row,
    # appended to live. True keeps it out of the list/suggestion/reconciliation
    # surfaces until Finish promotes it (flag cleared) or discards it (row
    # deleted). Direct by-id access deliberately still works — the capture UI
    # is built from the same endpoints as the recipe editor.
    is_draft: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=func.false(), default=False)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    ingredients = relationship("RecipeIngredient", back_populates="recipe", cascade="all,delete")
    cook_events = relationship("RecipeCookEvent", back_populates="recipe", cascade="all,delete")
    # The FK carries ON DELETE CASCADE, but that only fires for a DB-level
    # delete. `db.delete(recipe)` goes through the ORM, which won't touch a table
    # it has no relationship to — so without this, deleting a recipe orphaned its
    # picture and the bytes stayed in the database forever.
    # Deliberately NOT passive_deletes: that would hand the job to the FK's
    # ON DELETE CASCADE, which only fires where the database enforces foreign
    # keys. Letting the ORM issue the delete itself costs one extra statement
    # and is correct everywhere.
    image = relationship("RecipeImage", cascade="all,delete-orphan", uselist=False)
