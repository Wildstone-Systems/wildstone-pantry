"""ingredient substitutions + cook-event record (step 5 / ADR 0011 D5)

Revision ID: 0025_substitutions
Revises: 0024_recipe_servings
Create Date: 2026-07-23

Adds the learned ``ingredient_substitutions`` mapping (from→to, ratio, unit,
outcome, notes) and a ``recipe_cook_events.substitutions`` JSON column recording
what was swapped on each cook.
"""

import sqlalchemy as sa
from alembic import op

from app.core.db_types import JSONB, UUID

# revision identifiers
revision = "0025_substitutions"
down_revision = "0024_recipe_servings"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ingredient_substitutions",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("from_item_id", UUID(as_uuid=True), sa.ForeignKey("items.id", ondelete="CASCADE"), nullable=False),
        sa.Column("to_item_id", UUID(as_uuid=True), sa.ForeignKey("items.id", ondelete="CASCADE"), nullable=False),
        sa.Column("ratio", sa.Numeric(10, 4), nullable=False, server_default="1"),
        sa.Column("unit", sa.String(24), nullable=False, server_default="each"),
        sa.Column("outcome", sa.String(16), nullable=False, server_default="acceptable"),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("from_item_id", "to_item_id", name="uq_substitution_from_to"),
    )
    op.create_index(
        "ix_ingredient_substitutions_from_item_id", "ingredient_substitutions", ["from_item_id"]
    )
    op.add_column("recipe_cook_events", sa.Column("substitutions", JSONB, nullable=True))


def downgrade() -> None:
    op.drop_column("recipe_cook_events", "substitutions")
    op.drop_index("ix_ingredient_substitutions_from_item_id", table_name="ingredient_substitutions")
    op.drop_table("ingredient_substitutions")
