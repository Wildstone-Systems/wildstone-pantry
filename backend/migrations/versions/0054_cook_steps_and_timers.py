"""Cook-along: ticked-off steps, the cook plan, and kitchen timers (ADR 0026 §6.1).

Revision ID: 0054_cook_steps_timers
Revises: 0053_wildstone_integration
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0054_cook_steps_timers"
down_revision = "0053_wildstone_integration"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "cook_sessions",
        sa.Column("completed_steps", JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
    )
    op.add_column("cook_sessions", sa.Column("plan", JSONB, nullable=True))
    op.add_column("cook_sessions", sa.Column("version", sa.Integer(), nullable=False, server_default="0"))
    # One active cook at a time, enforced by the database. Any duplicates a
    # race left behind are settled first: the newest stays active.
    op.execute(
        """
        UPDATE cook_sessions SET status = 'abandoned', ended_at = now()
        WHERE status = 'active' AND id NOT IN (
            SELECT id FROM cook_sessions WHERE status = 'active' ORDER BY updated_at DESC LIMIT 1
        )
        """
    )
    op.create_index(
        "uq_cook_sessions_one_active",
        "cook_sessions",
        ["status"],
        unique=True,
        postgresql_where=sa.text("status = 'active'"),
    )

    op.create_table(
        "cook_timers",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("session_id", sa.Uuid(), sa.ForeignKey("cook_sessions.id", ondelete="SET NULL"), nullable=True),
        sa.Column("label", sa.String(length=80), nullable=False),
        sa.Column("step_number", sa.Integer(), nullable=True),
        sa.Column("duration_seconds", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="running"),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("stopped_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("set_by_member", sa.String(length=128), nullable=True),
        sa.Column("set_by_profile_id", sa.Uuid(), nullable=True),
    )
    op.create_index("ix_cook_timers_status_ends", "cook_timers", ["status", "ends_at"])


def downgrade() -> None:
    op.drop_index("uq_cook_sessions_one_active", table_name="cook_sessions")
    op.drop_column("cook_sessions", "version")
    op.drop_index("ix_cook_timers_status_ends", table_name="cook_timers")
    op.drop_table("cook_timers")
    op.drop_column("cook_sessions", "plan")
    op.drop_column("cook_sessions", "completed_steps")
