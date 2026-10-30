"""Recipe pictures: an external image_url on recipes, plus uploaded bytes.

Revision ID: 0046_recipe_images
Revises: 0045_cook_event_servings_made
"""

from alembic import op
import sqlalchemy as sa

revision = "0047_recipe_images"
down_revision = "0046_lot_targeted_tracking"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Where an imported recipe's picture lives upstream. TheMealDB has always
    # returned one (`strMealThumb`) and the import path was throwing it away.
    op.add_column("recipes", sa.Column("image_url", sa.String(length=500), nullable=True))

    op.create_table(
        "recipe_images",
        sa.Column("recipe_id", sa.Uuid(), nullable=False),
        sa.Column("content_type", sa.String(length=64), nullable=False),
        sa.Column("data", sa.LargeBinary(), nullable=False),
        sa.Column("width", sa.Integer(), nullable=True),
        sa.Column("height", sa.Integer(), nullable=True),
        sa.Column("byte_size", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["recipe_id"], ["recipes.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("recipe_id"),
    )


def downgrade() -> None:
    op.drop_table("recipe_images")
    op.drop_column("recipes", "image_url")
