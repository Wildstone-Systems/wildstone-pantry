from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db_types import JSONB, UUID
from app.models.base import Base


class ClassifierEvaluation(Base):
    """Short-lived shadow output only: never input state, prompts or credentials."""

    __tablename__ = "classifier_evaluations"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    result: Mapped[dict] = mapped_column(JSONB, nullable=False)


class ClassifierBenchmark(Base):
    """Bounded synthetic corpus review or run record; no private requests or keys."""

    __tablename__ = "classifier_benchmarks"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    kind: Mapped[str] = mapped_column(nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    revision: Mapped[int] = mapped_column(default=0, nullable=False)
    document: Mapped[dict] = mapped_column(JSONB, nullable=False)
