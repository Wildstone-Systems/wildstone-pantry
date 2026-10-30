"""inv_lot: container fields — ADR 0014 Phase 1, step 2

Revision ID: 0031_lot_container_fields
Revises: 0030_item_stock_mode
Create Date: 2026-07-24

Extend the (rebuildable) lot projection so a lot is a batch of identical
containers: ``container_size``/``container_unit`` (one physical container's net
content) and ``sealed_count`` (unopened containers). An open container is its own
lot row (status="open") whose ``quantity_remaining`` is the partial content left.
All nullable — legacy lots and pre-0014 events leave them null and keep their
prior behaviour. Being a projection, these repopulate on the next rebuild.
"""

import sqlalchemy as sa
from alembic import op

revision = "0031_lot_container_fields"
down_revision = "0030_item_stock_mode"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("inv_lot", sa.Column("container_size", sa.Numeric(12, 3), nullable=True))
    op.add_column("inv_lot", sa.Column("container_unit", sa.String(24), nullable=True))
    op.add_column("inv_lot", sa.Column("sealed_count", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("inv_lot", "sealed_count")
    op.drop_column("inv_lot", "container_unit")
    op.drop_column("inv_lot", "container_size")
