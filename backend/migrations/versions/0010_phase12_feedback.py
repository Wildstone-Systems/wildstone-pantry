"""Phase 12: feedback table

Revision ID: 0010_phase12_feedback
Revises: 0009_phase10_recipe_import
Create Date: 2026-02-13
"""

from alembic import op
import sqlalchemy as sa


revision = "0010_phase12_feedback"
down_revision = "0009_phase10_recipe_import"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "feedback",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("user_id", sa.dialects.postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("category", sa.String(length=24), nullable=False),
        sa.Column("route", sa.String(length=256), nullable=True),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False, server_default="open"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_feedback_user_created_at", "feedback", ["user_id", "created_at"])
    op.create_index("ix_feedback_status_created_at", "feedback", ["status", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_feedback_status_created_at", table_name="feedback")
    op.drop_index("ix_feedback_user_created_at", table_name="feedback")
    op.drop_table("feedback")

