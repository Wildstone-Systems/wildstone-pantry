"""Shopping-owned reviewed CSV drafts and durable source deduplication."""

from datetime import datetime
from uuid import UUID as PyUUID

from sqlalchemy import DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db_types import JSONB, UUID
from app.models.base import Base


class PurchaseReviewDraft(Base):
    __tablename__ = "purchase_review_drafts"
    id: Mapped[PyUUID] = mapped_column(UUID(), primary_key=True)
    actor_id: Mapped[PyUUID] = mapped_column(UUID(), ForeignKey("users.id", ondelete="CASCADE"))
    token_hash: Mapped[str] = mapped_column(String(64))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    revision: Mapped[int] = mapped_column(Integer, default=0)
    document: Mapped[dict] = mapped_column(JSONB())


class PurchaseCsvClaim(Base):
    __tablename__ = "purchase_csv_claims"
    location_id: Mapped[PyUUID] = mapped_column(
        UUID(), ForeignKey("inventory_locations.id", ondelete="CASCADE"), primary_key=True
    )
    source_fingerprint: Mapped[str] = mapped_column(String(64), primary_key=True)
    purchase_id: Mapped[PyUUID | None] = mapped_column(
        UUID(), ForeignKey("purchases.id", ondelete="CASCADE"), nullable=True
    )
