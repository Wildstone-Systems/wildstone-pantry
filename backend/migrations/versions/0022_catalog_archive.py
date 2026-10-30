"""catalog archive: items.is_active + inventory_locations.is_active

Revision ID: 0022_catalog_archive
Revises: 0021_agent_keys
Create Date: 2026-07-23

Soft-delete for the catalog. Instead of hard-deleting items and locations (which
cascades away recipe ingredients, inventory and history), the /catalog page
archives them: a recipe that references an archived item still resolves, and the
item can be reactivated or substituted. Existing rows backfill to active=true.
"""

import sqlalchemy as sa
from alembic import op

# revision identifiers
revision = "0022_catalog_archive"
down_revision = "0021_agent_keys"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "items",
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
    )
    op.add_column(
        "inventory_locations",
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
    )


def downgrade() -> None:
    op.drop_column("inventory_locations", "is_active")
    op.drop_column("items", "is_active")
