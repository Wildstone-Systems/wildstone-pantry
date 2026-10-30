"""Durable explicit receipt draft review and commit receipts."""

import sqlalchemy as sa
from alembic import op

from app.core.db_types import JSONB, UUID

revision = "0064_receipt_review_drafts"
down_revision = "0063_ranked_ingredient_reviews"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "receipt_review_drafts",
        sa.Column("id", UUID(), primary_key=True),
        sa.Column("actor_id", UUID(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("document", JSONB(), nullable=False),
    )
    op.create_index("ix_receipt_review_drafts_expires_at", "receipt_review_drafts", ["expires_at"])


def downgrade():
    op.drop_table("receipt_review_drafts")
