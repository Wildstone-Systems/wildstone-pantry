"""phase 3 recipe and consumption

Revision ID: 0004_phase3_recipe_consumption
Revises: 0003_phase2_receipt_barcode
Create Date: 2026-02-13

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0004_phase3_recipe_consumption"
down_revision: Union[str, Sequence[str], None] = "0003_phase2_receipt_barcode"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "recipe_ingredients",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("recipe_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("item_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("quantity_required", sa.Numeric(precision=12, scale=3), nullable=False),
        sa.Column("unit", sa.String(length=24), nullable=False, server_default="each"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["recipe_id"], ["recipes.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["item_id"], ["items.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_recipe_ingredients_recipe_id", "recipe_ingredients", ["recipe_id"], unique=False)
    op.create_index("ix_recipe_ingredients_item_id", "recipe_ingredients", ["item_id"], unique=False)

    op.create_table(
        "recipe_cook_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("recipe_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("location_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("servings", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("leftover_servings", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["recipe_id"], ["recipes.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["location_id"], ["inventory_locations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_recipe_cook_events_recipe_id", "recipe_cook_events", ["recipe_id"], unique=False)
    op.create_index("ix_recipe_cook_events_location_id", "recipe_cook_events", ["location_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_recipe_cook_events_location_id", table_name="recipe_cook_events")
    op.drop_index("ix_recipe_cook_events_recipe_id", table_name="recipe_cook_events")
    op.drop_table("recipe_cook_events")

    op.drop_index("ix_recipe_ingredients_item_id", table_name="recipe_ingredients")
    op.drop_index("ix_recipe_ingredients_recipe_id", table_name="recipe_ingredients")
    op.drop_table("recipe_ingredients")
