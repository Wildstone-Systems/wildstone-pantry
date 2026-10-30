from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import Boolean, DateTime, ForeignKey, Numeric, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db_types import UUID
from app.models.base import Base


class HouseholdProfile(Base):
    """A person in the household (ADR 0004).

    Profiles are the attribution target for inventory events and per-person
    calorie tracking. They are NOT login accounts — kids and family members have
    a profile with no password and are selected friction-free on the kiosk. A
    profile may optionally link to an admin ``account`` (``account_id``) for an
    adult who both gets tracked and administers the system.
    """

    __tablename__ = "household_profiles"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    display_name: Mapped[str] = mapped_column(String(120), nullable=False)
    # Optional link to an admin login (app.models.User). SET NULL so deleting an
    # account leaves the person/profile intact.
    account_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    # Forward-looking for the calorie feature (step 4); optional.
    daily_calorie_target: Mapped[Decimal | None] = mapped_column(Numeric(7, 1), nullable=True)
    # Optional kiosk PIN (ADR 0004 push C). Nullable = no PIN (free to select).
    # Stored as a salted scrypt hash; the raw PIN is never persisted.
    pin_hash: Mapped[str | None] = mapped_column(String(255), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    @property
    def has_pin(self) -> bool:
        """Whether selecting this profile requires a PIN (the hash is never exposed)."""
        return self.pin_hash is not None
