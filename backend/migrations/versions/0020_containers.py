"""containers — named sub-divisions of a location + per-lot placement (GH #9)

Revision ID: 0020_containers
Revises: 0019_connector_config
Create Date: 2026-07-23

Ported from the pre-fork lineage onto this trunk's migration chain. Adds:
- containers: id, location_id (FK CASCADE), name, description
- inventory_events.container_id: placement recorded at intake (no FK; the log is
  immutable, so a deleted container must not mutate history)
- inv_lot.container_id: derived placement on the lot (no FK; rebuildable projection)
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

# revision identifiers
revision = "0020_containers"
down_revision = "0019_connector_config"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "containers",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "location_id",
            UUID(as_uuid=True),
            sa.ForeignKey("inventory_locations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_containers_location_id", "containers", ["location_id"])
    op.add_column("inventory_events", sa.Column("container_id", UUID(as_uuid=True), nullable=True))
    op.add_column("inv_lot", sa.Column("container_id", UUID(as_uuid=True), nullable=True))


def downgrade() -> None:
    op.drop_column("inv_lot", "container_id")
    op.drop_column("inventory_events", "container_id")
    op.drop_index("ix_containers_location_id", table_name="containers")
    op.drop_table("containers")
