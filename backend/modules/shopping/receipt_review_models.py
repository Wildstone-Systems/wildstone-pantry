"""Shopping-owned durable draft and exactly-once commit receipt."""

from datetime import datetime
from uuid import UUID as PyUUID

from sqlalchemy import DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db_types import JSONB, UUID
from app.models.base import Base


class ReceiptReviewDraft(Base):
    __tablename__ = "receipt_review_drafts"
    id: Mapped[PyUUID] = mapped_column(UUID(), primary_key=True)
    actor_id: Mapped[PyUUID] = mapped_column(UUID(), ForeignKey("users.id", ondelete="CASCADE"))
    token_hash: Mapped[str] = mapped_column(String(64))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    revision: Mapped[int] = mapped_column(Integer, default=0)
    document: Mapped[dict] = mapped_column(JSONB())
