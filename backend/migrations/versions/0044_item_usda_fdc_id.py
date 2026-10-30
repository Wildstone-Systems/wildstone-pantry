"""items.usda_fdc_id — stable identity for a USDA-generic-food item

Logging a meal that's neither in the pantry catalog nor has a barcode (e.g.
"3 hard boiled eggs") auto-creates a lightweight, barcode-less catalog Item
from a USDA FoodData Central generic food. Without a stable identity, picking
the same food again next week would create a duplicate Item every time.
usda_fdc_id is that identity — nullable (unrelated items aren't touched) and
unique (NULLs don't collide), so re-picking the same food reuses one Item.
"""

import sqlalchemy as sa
from alembic import op

revision = "0044_item_usda_fdc_id"
down_revision = "0043_recipe_external_identity"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("items", sa.Column("usda_fdc_id", sa.Integer(), nullable=True))
    op.create_unique_constraint("uq_items_usda_fdc_id", "items", ["usda_fdc_id"])


def downgrade() -> None:
    op.drop_constraint("uq_items_usda_fdc_id", "items", type_="unique")
    op.drop_column("items", "usda_fdc_id")
