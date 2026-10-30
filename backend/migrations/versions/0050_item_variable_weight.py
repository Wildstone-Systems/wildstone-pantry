"""Catch weight: one UPC, a different weight in every package (ADR 0025 §1).

Revision ID: 0050_item_variable_weight
Revises: 0049_recipe_yield_ratio
"""

import sqlalchemy as sa
from alembic import op

revision = "0050_item_variable_weight"
down_revision = "0049_recipe_yield_ratio"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # False everywhere on backfill: an existing item's size_quantity is being
    # used as its real net contents today, and flipping that meaning under a
    # stocked item would change what its balance means. Marking a product as
    # variable-weight stays an explicit act.
    op.add_column(
        "items",
        sa.Column("variable_weight", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column("items", "variable_weight")
