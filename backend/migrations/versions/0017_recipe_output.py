"""recipe output/yield (step 4a: transformation engine)

Revision ID: 0017_recipe_output
Revises: 0016_shopping_list_restock
Create Date: 2026-07-22

Cooking a recipe becomes a transformation that can *produce* new tracked
inventory (bread out of flour/water/salt/yeast). Adds the optional output spec
to the recipes table:
- output_item_id: catalog item the recipe produces (FK items.id, SET NULL)
- output_quantity: quantity produced per serving
- output_unit: unit of the produced quantity
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

# revision identifiers
revision = "0017_recipe_output"
down_revision = "0016_shopping_list_restock"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("recipes", sa.Column("output_item_id", UUID(as_uuid=True), nullable=True))
    op.add_column("recipes", sa.Column("output_quantity", sa.Numeric(12, 3), nullable=True))
    op.add_column("recipes", sa.Column("output_unit", sa.String(24), nullable=True))
    op.create_foreign_key(
        "fk_recipes_output_item_id_items",
        "recipes",
        "items",
        ["output_item_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint("fk_recipes_output_item_id_items", "recipes", type_="foreignkey")
    op.drop_column("recipes", "output_unit")
    op.drop_column("recipes", "output_quantity")
    op.drop_column("recipes", "output_item_id")
