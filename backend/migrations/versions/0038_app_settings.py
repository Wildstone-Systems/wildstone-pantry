"""app_settings — instance-level key/value store for wizard + runtime toggles (ADR 0018 §7)

Backs the first-run wizard (setup_completed, household_name, lan_open_ack) and
the accounts-mode override (auth_enabled_override) so choosing "household
accounts" in the wizard survives restarts without editing env files.
"""

import sqlalchemy as sa
from alembic import op

revision = "0038_app_settings"
down_revision = "0037_drop_license_audits"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "app_settings",
        sa.Column("key", sa.String(length=64), primary_key=True),
        sa.Column("value", sa.Text(), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )


def downgrade() -> None:
    op.drop_table("app_settings")
