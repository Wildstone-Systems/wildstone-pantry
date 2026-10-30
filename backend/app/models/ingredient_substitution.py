from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import Boolean, DateTime, ForeignKey, Numeric, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db_types import UUID
from app.models.base import Base


class IngredientSubstitution(Base):
    """A directional "use B instead of A" mapping (ADR 0011 D5, canonicalised in
    ADR 0015 Phase 3).

    Keyed on the **canonical ingredient** (``from_canonical`` → ``to_canonical``)
    so an approved sub generalises across brands without losing flavor: the ``to``
    key keeps its modifiers (``greek plain yogurt``, not ``greek yogurt``) so it
    matches your plain tub and not the strawberry one. Two sources feed the same
    table — a curated **seed** of common culinary subs (``approved=False`` until
    you confirm one) and **learned** ones captured at cook time (``approved=True``).
    The ``*_item_id`` columns are now optional provenance ("learned from these
    items"); seed rows carry canonical keys with no items. Directional — a
    reciprocal (B→A) is a separate row and is never written implicitly.
    """

    __tablename__ = "ingredient_substitutions"
    __table_args__ = (
        UniqueConstraint("from_item_id", "to_item_id", name="uq_substitution_from_to"),
        UniqueConstraint("from_canonical", "to_canonical", name="uq_substitution_from_to_canonical"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    # Optional provenance — the concrete items a learned sub came from. Null for
    # curated seed rows, which exist only as canonical keys.
    from_item_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("items.id", ondelete="CASCADE"), nullable=True, index=True
    )
    to_item_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("items.id", ondelete="CASCADE"), nullable=True
    )
    # Canonical identity keys — the matching basis (ADR 0015).
    from_canonical: Mapped[str | None] = mapped_column(String(200), nullable=True, index=True)
    to_canonical: Mapped[str | None] = mapped_column(String(200), nullable=True)
    # Human labels for display (canonical keys are token-sorted and ugly).
    from_label: Mapped[str | None] = mapped_column(String(200), nullable=True)
    to_label: Mapped[str | None] = mapped_column(String(200), nullable=True)
    # Learned subs (and any the user confirms) are approved and count toward
    # availability; seed subs are suggestions until approved.
    approved: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default=func.true())
    # Quantity of substitute per 1 unit of the original, expressed in ``unit``.
    ratio: Mapped[Decimal] = mapped_column(Numeric(10, 4), nullable=False, default=Decimal("1"))
    unit: Mapped[str] = mapped_column(String(24), nullable=False, default="each")
    # NULL preserves legacy ratios; new approvals explicitly bind the original unit.
    from_unit: Mapped[str | None] = mapped_column(String(24), nullable=True)
    # UI ranking: direct > acceptable > emergency.
    outcome: Mapped[str] = mapped_column(String(16), nullable=False, default="acceptable")
    notes: Mapped[str | None] = mapped_column(Text(), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    from_item = relationship("Item", foreign_keys=[from_item_id])
    to_item = relationship("Item", foreign_keys=[to_item_id])
