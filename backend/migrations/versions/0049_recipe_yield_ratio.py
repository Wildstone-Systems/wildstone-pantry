"""Derived yield: a recipe's output as a ratio of the mass that went in (ADR 0025 §3).

Revision ID: 0049_recipe_yield_ratio
Revises: 0048_recipe_is_draft
"""

import sqlalchemy as sa
from alembic import op

revision = "0049_recipe_yield_ratio"
down_revision = "0048_recipe_is_draft"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 'fixed' (output_quantity × servings) is the pre-existing behaviour and the
    # default, so every recipe already in the table keeps producing exactly what
    # it produced yesterday.
    op.add_column(
        "recipes",
        sa.Column("output_mode", sa.String(length=16), nullable=False, server_default="fixed"),
    )
    op.add_column("recipes", sa.Column("output_yield_ratio", sa.Numeric(6, 4), nullable=True))
    # Which ingredient line the ratio applies to. False everywhere on backfill:
    # no existing recipe uses 'yield_ratio', and the cook refuses that mode
    # without a basis rather than guessing one.
    op.add_column(
        "recipe_ingredients",
        sa.Column("yield_basis", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column("recipe_ingredients", "yield_basis")
    op.drop_column("recipes", "output_yield_ratio")
    op.drop_column("recipes", "output_mode")
