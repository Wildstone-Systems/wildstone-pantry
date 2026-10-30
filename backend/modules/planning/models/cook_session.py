from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import DateTime, ForeignKey, Index, Integer, Numeric, String, func, text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db_types import JSONB, UUID
from app.models.base import Base


class CookSession(Base):
    """One person cooking one recipe, step by step (ADR 0026 §6).

    Holds progress and the plan — recipe, servings, current step, the steps
    ticked off, and how the cook was set up (ingredient overrides, "use it
    all", "use what I have"). Stock is never touched until the session is
    finished, which calls the ordinary cook path (ADR 0022 §4) with that plan,
    so abandoning a session costs nothing and whoever finishes it — the tablet
    or an agent — cooks what was set up.
    """

    __tablename__ = "cook_sessions"
    # One active cook at a time, enforced by the database rather than by the
    # code that starts one: a tablet and an agent starting at once cannot
    # both end up with an active session (ADR 0026 §6.1).
    __table_args__ = (
        Index(
            "uq_cook_sessions_one_active",
            "status",
            unique=True,
            postgresql_where=text("status = 'active'"),
            sqlite_where=text("status = 'active'"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    recipe_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("recipes.id", ondelete="CASCADE"), nullable=False
    )
    # Absolute servings to make (ADR 0022 §1); null = the recipe as written.
    servings_made: Mapped[Decimal | None] = mapped_column(Numeric(12, 3), nullable=True)
    # Zero-based index into the recipe's derived steps.
    current_step: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # active | finished | abandoned
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active")
    actor: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # Bumped by every change to progress or plan: a write only lands on the
    # version it read, so the tablet and an agent ticking steps at once both
    # keep their tick instead of one overwriting the other.
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    # 1-based step numbers the cook has ticked off, in no particular order.
    completed_steps: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    # How the cook was set up on the tablet (ADR 0022 §2, ADR 0025 §5/§7):
    # {"ingredient_overrides": [...], "scale_to_ingredient_id": str|None,
    #  "allow_short_ingredients": bool}. Null = the recipe as written.
    plan: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    # What Finish reported, so a retried Finish can report it again.
    result: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    cook_event_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class CookTimer(Base):
    """A kitchen timer (ADR 0026 §6.1).

    Usually set during a cook — by a tap on the tablet or by an agent ("set a
    timer for the pasta") — and then tied to that session and step, but a
    timer outlives its session: the pasta still needs draining after the
    cook-along is stopped.

    ``status`` is ``running`` until someone stops it: ``cancelled`` before it
    went off, ``dismissed`` after. "Ringing" is not stored — it is a running
    timer whose ``ends_at`` has passed.
    """

    __tablename__ = "cook_timers"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    session_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("cook_sessions.id", ondelete="SET NULL"), nullable=True
    )
    label: Mapped[str] = mapped_column(String(80), nullable=False)
    step_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    duration_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="running")
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    stopped_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Who set it, so "your timer is done" reaches them (ADR 0026 §6.1): the
    # Wildstone member an agent was acting for, or the household profile
    # picked on the tablet. Either may be unknown.
    set_by_member: Mapped[str | None] = mapped_column(String(128), nullable=True)
    set_by_profile_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
