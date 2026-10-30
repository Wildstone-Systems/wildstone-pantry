"""Server-side cook sessions (ADR 0026 §6).

Revision ID: 0052_cook_sessions
Revises: 0051_recipe_whole_package_lines
"""

import sqlalchemy as sa
from alembic import op

revision = "0052_cook_sessions"
down_revision = "0051_recipe_whole_package_lines"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "cook_sessions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "recipe_id",
            sa.Uuid(),
            sa.ForeignKey("recipes.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("servings_made", sa.Numeric(12, 3), nullable=True),
        sa.Column("current_step", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="active"),
        sa.Column("actor", sa.String(length=64), nullable=True),
        sa.Column("cook_event_id", sa.Uuid(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_cook_sessions_status", "cook_sessions", ["status"])


def downgrade() -> None:
    op.drop_index("ix_cook_sessions_status", table_name="cook_sessions")
    op.drop_table("cook_sessions")
