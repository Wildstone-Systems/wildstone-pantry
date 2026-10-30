"""connector config (ADR 0009: in-app, encrypted vendor-connector credentials)

Revision ID: 0019_connector_config
Revises: 0018_item_calories
Create Date: 2026-07-23

Adds ``connector_config`` — one row per connector key (e.g. "imap") holding a
Fernet-encrypted JSON blob of that connector's credentials, set via the admin
Integrations screen. Ciphertext only; secrets are never stored in the clear.
"""

import sqlalchemy as sa
from alembic import op

# revision identifiers
revision = "0019_connector_config"
down_revision = "0018_item_calories"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "connector_config",
        sa.Column("key", sa.String(length=64), primary_key=True),
        sa.Column("ciphertext", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("connector_config")
