"""recipe base servings (step 5 / ADR 0011 D3)

Revision ID: 0024_recipe_servings
Revises: 0023_ingredient_reference
Create Date: 2026-07-23

Adds recipes.servings (base yield) — the divisor for per-serving nutrition and the
default cook quantity. Does not change cook decrement math (cook's ``servings``
argument stays "servings to make").
"""

import sqlalchemy as sa
from alembic import op

# revision identifiers
revision = "0024_recipe_servings"
down_revision = "0023_ingredient_reference"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "recipes",
        sa.Column("servings", sa.Integer(), nullable=False, server_default="1"),
    )


def downgrade() -> None:
    op.drop_column("recipes", "servings")
