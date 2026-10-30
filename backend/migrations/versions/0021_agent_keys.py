"""add agent_keys (long-lived agent credentials) — ported (GH #11)

Revision ID: 0021_agent_keys
Revises: 0020_containers
Create Date: 2026-07-23

Long-lived, revocable credentials for autonomous agents. Only the SHA-256 hash
of the secret is stored; the plaintext is shown once at creation. Renumbered onto
this trunk's chain during the branch reconciliation.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

# revision identifiers
revision = "0021_agent_keys"
down_revision = "0020_containers"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "agent_keys",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("token_prefix", sa.String(16), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("scopes", sa.String(255), nullable=False, server_default="read,write"),
        sa.Column("revoked", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_agent_keys_token_hash", "agent_keys", ["token_hash"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_agent_keys_token_hash", table_name="agent_keys")
    op.drop_table("agent_keys")
