"""items: nutrition_estimated flag

Revision ID: 0029_nutrition_estimated
Revises: 0028_shopping_chosen_offer
Create Date: 2026-07-24

When no provider carries label nutrition for an item's UPC — common for retailer
house brands, whose UPCs OFF/USDA don't index — we fall back to GENERIC per-100g
macros matched by NAME from USDA's non-branded datasets. Those are approximate,
so we flag them: the UI shows 'Estimated' and never presents borrowed macros as
if they were read off this product's own label.
"""

import sqlalchemy as sa
from alembic import op

revision = "0029_nutrition_estimated"
down_revision = "0028_shopping_chosen_offer"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "items",
        sa.Column("nutrition_estimated", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column("items", "nutrition_estimated")
