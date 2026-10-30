"""household profiles (ADR 0004: person/login split, kiosk vs admin)

Revision ID: 0013_household_profiles
Revises: 0012_inventory_events
Create Date: 2026-07-22

Adds the ``household_profiles`` table: lightweight persons (no password) that
are the attribution target for inventory events and per-person calorie tracking.
A profile may optionally link to an admin login (``account_id`` -> users.id).
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

# revision identifiers
revision = "0013_household_profiles"
down_revision = "0012_inventory_events"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "household_profiles",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("display_name", sa.String(120), nullable=False),
        sa.Column("account_id", UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("daily_calorie_target", sa.Numeric(7, 1), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table("household_profiles")
