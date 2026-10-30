"""Recipe-scoped reviewed ingredient choice; no changes to food authorities."""

import sqlalchemy as sa
from alembic import op

from app.core.db_types import JSONB, UUID

revision = "0061_ingredient_match_reviews"
down_revision = "0060_classifier_benchmarks"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "ingredient_match_scopes",
        sa.Column(
            "ingredient_id", UUID(), sa.ForeignKey("recipe_ingredients.id", ondelete="CASCADE"), primary_key=True
        ),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("current_review_id", UUID(), nullable=True),
    )
    op.create_table(
        "ingredient_match_reviews",
        sa.Column("id", UUID(), primary_key=True),
        sa.Column("recipe_id", UUID(), sa.ForeignKey("recipes.id", ondelete="CASCADE"), nullable=False),
        sa.Column("ingredient_id", UUID(), sa.ForeignKey("recipe_ingredients.id", ondelete="CASCADE"), nullable=False),
        sa.Column("candidate_item_id", UUID(), sa.ForeignKey("items.id", ondelete="CASCADE"), nullable=False),
        sa.Column("actor_id", UUID(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("status", sa.String(24), nullable=False, server_default="pending"),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("document", JSONB(), nullable=False),
    )
    for column in ("recipe_id", "ingredient_id", "expires_at"):
        op.create_index("ix_ingredient_match_reviews_" + column, "ingredient_match_reviews", [column])


def downgrade():
    op.drop_table("ingredient_match_reviews")
    op.drop_table("ingredient_match_scopes")
