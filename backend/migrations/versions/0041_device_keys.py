"""device_keys — per-device credentials for phones/tablets (ADR 0018 §8)

Agent-key pattern (hashed at rest, revocable, no expiry) but bound to a user,
so a family member's phone is *them*. Minted only via the pairing flow.
"""

import sqlalchemy as sa
from alembic import op

revision = "0041_device_keys"
down_revision = "0040_integration_connections"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "device_keys",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("key_hash", sa.String(length=64), nullable=False, unique=True),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("device_keys")
