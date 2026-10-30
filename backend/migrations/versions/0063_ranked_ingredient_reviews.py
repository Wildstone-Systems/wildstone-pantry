"""Explicit ranked household identity review receipts."""

import sqlalchemy as sa
from alembic import op

from app.core.db_types import JSONB, UUID

revision = "0063_ranked_ingredient_reviews"
down_revision = "0062_item_product_evidence"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "ranked_ingredient_reviews",
        sa.Column("id", UUID(), primary_key=True),
        sa.Column("recipe_id", UUID(), sa.ForeignKey("recipes.id", ondelete="CASCADE"), nullable=False),
        sa.Column("ingredient_id", UUID(), sa.ForeignKey("recipe_ingredients.id", ondelete="CASCADE"), nullable=False),
        sa.Column("actor_id", UUID(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("document", JSONB(), nullable=False),
    )
    op.create_index("ix_ranked_ingredient_reviews_expires_at", "ranked_ingredient_reviews", ["expires_at"])


def downgrade():
    op.drop_table("ranked_ingredient_reviews")
