"""drop PantryOS Connect tables (entitlement_state, integration_connections)

The hosted subscription layer (activation keys, entitlement tokens, brokered
store connections) was removed; its two tables have no owner any more.
Downgrade recreates them empty.
"""

import sqlalchemy as sa
from alembic import op

revision = "0073_drop_pantryos_connect"
down_revision = "0072_recipe_nutrition_override"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_table("integration_connections")
    op.drop_table("entitlement_state")


def downgrade() -> None:
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
