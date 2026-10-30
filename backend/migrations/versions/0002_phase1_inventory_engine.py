"""phase 1 inventory engine

Revision ID: 0002_phase1_inventory_engine
Revises: 0001_phase0_base_schema
Create Date: 2026-02-13

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0002_phase1_inventory_engine"
down_revision: Union[str, Sequence[str], None] = "0001_phase0_base_schema"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "inventory",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("item_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("location_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("quantity", sa.Numeric(precision=12, scale=3), nullable=False, server_default="0"),
        sa.Column("unit", sa.String(length=24), nullable=False, server_default="each"),
        sa.Column("expiration_date", sa.Date(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["item_id"], ["items.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["location_id"], ["inventory_locations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("item_id", "location_id", name="uq_inventory_item_location"),
    )
    op.create_index("ix_inventory_item_id", "inventory", ["item_id"], unique=False)
    op.create_index("ix_inventory_location_id", "inventory", ["location_id"], unique=False)
    op.create_index("ix_inventory_expiration_date", "inventory", ["expiration_date"], unique=False)

    op.add_column("inventory_transactions", sa.Column("reason", sa.String(length=120), nullable=True))


def downgrade() -> None:
    op.drop_column("inventory_transactions", "reason")
    op.drop_index("ix_inventory_expiration_date", table_name="inventory")
    op.drop_index("ix_inventory_location_id", table_name="inventory")
    op.drop_index("ix_inventory_item_id", table_name="inventory")
    op.drop_table("inventory")
