"""Meal plan shopping bridge: always-skipped ingredients (epic #28, #31)."""

import sqlalchemy as sa
from alembic import op

from app.core.db_types import UUID

revision = "0069_meal_plan_excluded_items"
down_revision = "0068_meal_plan_entries"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "meal_plan_excluded_items",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("key", sa.String(200), nullable=False, unique=True),
        sa.Column("label", sa.String(200), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )


def downgrade():
    op.drop_table("meal_plan_excluded_items")
