"""Whole-package recipe lines: "one roast, whatever it weighs" (ADR 0025 §5).

Revision ID: 0051_recipe_whole_package_lines
Revises: 0050_item_variable_weight
"""

import sqlalchemy as sa
from alembic import op

revision = "0051_recipe_whole_package_lines"
down_revision = "0050_item_variable_weight"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 'amount' is what every existing line means today, so the backfill changes
    # no recipe's behaviour.
    op.add_column(
        "recipe_ingredients",
        sa.Column("quantity_mode", sa.String(length=16), nullable=False, server_default="amount"),
    )


def downgrade() -> None:
    op.drop_column("recipe_ingredients", "quantity_mode")
