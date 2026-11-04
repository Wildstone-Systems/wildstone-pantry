"""Wildstone relay remembers the meal plan it last reported (epic #28, #33)."""

import sqlalchemy as sa
from alembic import op

revision = "0070_wildstone_meal_plan_state"
down_revision = "0069_meal_plan_excluded_items"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("wildstone_relay_state", sa.Column("meal_plan_fingerprint", sa.String(64), nullable=True))


def downgrade():
    op.drop_column("wildstone_relay_state", "meal_plan_fingerprint")
