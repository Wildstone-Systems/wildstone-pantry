"""drop license_audits — the shared-password license gate is removed (ADR 0018)

The legacy ``LICENSE_KEY`` gate (Phase 5) audited validation attempts into
``license_audits``. ADR 0018 removes that gate entirely — access control is
real auth (``AUTH_ENABLED``), and "license" now only ever means the activation
key for optional cloud services, which never gates local features. The table
was append-only and never read back; nothing to migrate.
"""

import sqlalchemy as sa
from alembic import op

revision = "0037_drop_license_audits"
down_revision = "0036_recipe_optional_units"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_table("license_audits")


def downgrade() -> None:
    op.create_table(
        "license_audits",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("license_key_prefix", sa.String(length=16), nullable=False),
        sa.Column("valid", sa.Boolean(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
