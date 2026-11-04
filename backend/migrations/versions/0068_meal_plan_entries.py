"""Meal plan calendar: meal_plan_entries (epic #28, #29)."""

import sqlalchemy as sa
from alembic import op

from app.core.db_types import UUID

revision = "0068_meal_plan_entries"
down_revision = "0067_scoped_substitutions"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "meal_plan_entries",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("plan_date", sa.Date(), nullable=False),
        sa.Column("meal_type", sa.String(16), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("recipe_id", UUID(as_uuid=True), sa.ForeignKey("recipes.id", ondelete="SET NULL"), nullable=True),
        sa.Column("item_id", UUID(as_uuid=True), sa.ForeignKey("items.id", ondelete="SET NULL"), nullable=True),
        sa.Column("title", sa.String(200), nullable=True),
        sa.Column("servings", sa.Integer(), nullable=False, server_default="1"),
        sa.Column(
            "profile_id", UUID(as_uuid=True), sa.ForeignKey("household_profiles.id", ondelete="SET NULL"), nullable=True
        ),
        sa.Column("status", sa.String(16), nullable=False, server_default="planned"),
        sa.Column(
            "cook_event_id",
            UUID(as_uuid=True),
            sa.ForeignKey("recipe_cook_events.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("source", sa.String(16), nullable=False, server_default="manual"),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("created_by", sa.String(64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint(
            "recipe_id IS NOT NULL OR item_id IS NOT NULL OR title IS NOT NULL",
            name="ck_meal_plan_entries_names_a_meal",
        ),
        sa.CheckConstraint("servings >= 1", name="ck_meal_plan_entries_servings_positive"),
    )
    op.create_index("ix_meal_plan_entries_plan_date", "meal_plan_entries", ["plan_date"])
    op.create_index("ix_meal_plan_entries_recipe_id", "meal_plan_entries", ["recipe_id"])


def downgrade():
    op.drop_index("ix_meal_plan_entries_recipe_id", table_name="meal_plan_entries")
    op.drop_index("ix_meal_plan_entries_plan_date", table_name="meal_plan_entries")
    op.drop_table("meal_plan_entries")
