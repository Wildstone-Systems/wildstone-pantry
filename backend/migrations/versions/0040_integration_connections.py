"""integration_connections — brokered store connections (ADR 0018 §3)

One row per provider. Brokered mode stores only the broker connection ref and
a short-lived access-token cache; refresh tokens live exclusively on the
broker (hybrid custody).
"""

import sqlalchemy as sa
from alembic import op

revision = "0040_integration_connections"
down_revision = "0039_entitlement_state"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "integration_connections",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("provider", sa.String(length=32), nullable=False, unique=True),
        sa.Column("mode", sa.String(length=16), nullable=False, server_default="brokered"),
        sa.Column("broker_ref", sa.String(length=64), nullable=False),
        sa.Column("scopes", sa.String(length=255), nullable=True),
        sa.Column("external_hint", sa.String(length=255), nullable=True),
        sa.Column("access_token_ciphertext", sa.Text(), nullable=True),
        sa.Column("access_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="active"),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("integration_connections")
