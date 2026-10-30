"""household profile PIN (ADR 0004 push C)

Revision ID: 0014_household_profile_pin
Revises: 0013_household_profiles
Create Date: 2026-07-22

Adds an optional salted-scrypt ``pin_hash`` to household profiles. Null = no PIN
(the profile is free to select on the kiosk).
"""

from alembic import op
import sqlalchemy as sa

# revision identifiers
revision = "0014_household_profile_pin"
down_revision = "0013_household_profiles"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("household_profiles", sa.Column("pin_hash", sa.String(255), nullable=True))


def downgrade() -> None:
    op.drop_column("household_profiles", "pin_hash")
