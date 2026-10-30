"""user.is_provisioned for kiosk-mode admin bootstrap (ADR 0004)

Revision ID: 0015_user_is_provisioned
Revises: 0014_household_profile_pin
Create Date: 2026-07-22

Distinguishes a human-provisioned admin (bootstrap) from the kiosk personal-mode
auto-user, so first-run admin setup works even after the kiosk auto-created a
personal user. Existing rows default to provisioned=true (they predate the flag
and are treated as real accounts).
"""

from alembic import op
import sqlalchemy as sa

# revision identifiers
revision = "0015_user_is_provisioned"
down_revision = "0014_household_profile_pin"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("is_provisioned", sa.Boolean(), nullable=False, server_default=sa.true()),
    )
    # New rows default to false in the ORM; the column default only backfills
    # pre-existing accounts as provisioned.
    op.alter_column("users", "is_provisioned", server_default=sa.false())


def downgrade() -> None:
    op.drop_column("users", "is_provisioned")
