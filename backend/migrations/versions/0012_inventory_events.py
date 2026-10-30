"""inventory event store + projections (ADR 0002, Phase 4 step 1)

Revision ID: 0012_inventory_events
Revises: 0011_barcode_lookup
Create Date: 2026-06-24

Adds the append-only inventory event log and its derived projection tables
ALONGSIDE the legacy inventory / inventory_transactions tables. Nothing reads or
writes these yet — this is the additive first step of the event-sourcing
migration.
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

# revision identifiers
revision = "0012_inventory_events"
down_revision = "0011_barcode_lookup"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "inventory_events",
        sa.Column("seq", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("event_id", UUID(as_uuid=True), nullable=False),
        sa.Column("event_type", sa.String(32), nullable=False),
        sa.Column("item_id", UUID(as_uuid=True), sa.ForeignKey("items.id", ondelete="CASCADE"), nullable=False),
        sa.Column(
            "location_id",
            UUID(as_uuid=True),
            sa.ForeignKey("inventory_locations.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "target_location_id",
            UUID(as_uuid=True),
            sa.ForeignKey("inventory_locations.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("lot_id", UUID(as_uuid=True), nullable=True),
        sa.Column("quantity", sa.Numeric(12, 3), nullable=False, server_default="0"),
        sa.Column("unit", sa.String(24), nullable=False, server_default="each"),
        sa.Column("unit_price", sa.Numeric(12, 4), nullable=True),
        sa.Column("expiration_date", sa.Date(), nullable=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("actor", sa.String(64), nullable=True),
        sa.Column("source", sa.String(32), nullable=False, server_default="api"),
        sa.Column("correlation_id", UUID(as_uuid=True), nullable=True),
        sa.Column("reason", sa.String(120), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("payload", JSONB, nullable=True),
        sa.UniqueConstraint("event_id", name="uq_inventory_events_event_id"),
    )
    op.create_index(
        "ix_inventory_events_item_location_seq",
        "inventory_events",
        ["item_id", "location_id", "seq"],
    )

    op.create_table(
        "inv_balance",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("item_id", UUID(as_uuid=True), sa.ForeignKey("items.id", ondelete="CASCADE"), nullable=False),
        sa.Column(
            "location_id",
            UUID(as_uuid=True),
            sa.ForeignKey("inventory_locations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("quantity", sa.Numeric(12, 3), nullable=False, server_default="0"),
        sa.Column("unit", sa.String(24), nullable=False, server_default="each"),
        sa.Column("last_applied_seq", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("item_id", "location_id", name="uq_inv_balance_item_location"),
    )

    op.create_table(
        "inv_lot",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("item_id", UUID(as_uuid=True), sa.ForeignKey("items.id", ondelete="CASCADE"), nullable=False),
        sa.Column(
            "location_id",
            UUID(as_uuid=True),
            sa.ForeignKey("inventory_locations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("quantity_remaining", sa.Numeric(12, 3), nullable=False, server_default="0"),
        sa.Column("unit", sa.String(24), nullable=False, server_default="each"),
        sa.Column("expiration_date", sa.Date(), nullable=True),
        sa.Column("unit_price", sa.Numeric(12, 4), nullable=True),
        sa.Column("status", sa.String(16), nullable=False, server_default="sealed"),
        sa.Column("frozen", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("source_event_id", UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("last_applied_seq", sa.BigInteger(), nullable=False, server_default="0"),
    )
    op.create_index("ix_inv_lot_item_location", "inv_lot", ["item_id", "location_id"])

    op.create_table(
        "inv_projection_state",
        sa.Column("name", sa.String(64), primary_key=True),
        sa.Column("last_applied_seq", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table("inv_projection_state")
    op.drop_index("ix_inv_lot_item_location", table_name="inv_lot")
    op.drop_table("inv_lot")
    op.drop_table("inv_balance")
    op.drop_index("ix_inventory_events_item_location_seq", table_name="inventory_events")
    op.drop_table("inventory_events")
