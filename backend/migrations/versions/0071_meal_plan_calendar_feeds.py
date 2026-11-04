"""Meal plan read-only iCal feed secret (epic #28, #33)."""

import sqlalchemy as sa
from alembic import op

from app.core.db_types import UUID

revision = "0071_meal_plan_calendar_feeds"
down_revision = "0070_wildstone_meal_plan_state"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "meal_plan_calendar_feeds",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )


def downgrade():
    op.drop_table("meal_plan_calendar_feeds")
