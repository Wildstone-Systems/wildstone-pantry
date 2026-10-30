"""entitlement_state — the instance's activation record (ADR 0018 §1–§2)

Persists the signed entitlement token, the instance keypair (private key
Fernet-encrypted), and refresh bookkeeping so premium features survive
restarts and work offline through the 30-day validity + 14-day grace window.
"""

import sqlalchemy as sa
from alembic import op

revision = "0039_entitlement_state"
down_revision = "0038_app_settings"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "entitlement_state",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("instance_id", sa.String(length=64), nullable=False),
        sa.Column("key_hint", sa.String(length=8), nullable=False),
        sa.Column("tier", sa.String(length=32), nullable=False),
        sa.Column("token", sa.Text(), nullable=False),
        sa.Column("private_key_ciphertext", sa.Text(), nullable=False),
        sa.Column("server_url", sa.String(length=255), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_refresh_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("refresh_failures", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table("entitlement_state")
