"""Reusable user-provided product facts, without catalog truth rewriting."""

import sqlalchemy as sa
from alembic import op

from app.core.db_types import JSONB, UUID

revision = "0062_item_product_evidence"
down_revision = "0061_ingredient_match_reviews"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "item_product_evidence",
        sa.Column("item_id", UUID(), sa.ForeignKey("items.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("base_source_hash", sa.String(64), nullable=False),
        sa.Column("facts", JSONB(), nullable=False),
        sa.Column("updated_by", UUID(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade():
    op.drop_table("item_product_evidence")
