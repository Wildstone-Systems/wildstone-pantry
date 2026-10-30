"""Optional descriptive user facts, separate from catalog identity authorities."""

from datetime import datetime
from uuid import UUID as PythonUUID

from sqlalchemy import DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db_types import JSONB, UUID
from app.models.base import Base


class ItemProductEvidence(Base):
    __tablename__ = "item_product_evidence"

    item_id: Mapped[PythonUUID] = mapped_column(UUID(), ForeignKey("items.id", ondelete="CASCADE"), primary_key=True)
    revision: Mapped[int] = mapped_column(Integer)
    base_source_hash: Mapped[str] = mapped_column(String(64))
    facts: Mapped[dict] = mapped_column(JSONB)
    updated_by: Mapped[PythonUUID | None] = mapped_column(
        UUID(), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
