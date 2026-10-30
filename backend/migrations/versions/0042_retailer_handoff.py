"""retailer_links + handoff sessions + product maps (ADR 0019 Phase 1)

Search-link retailers become data rows; the handoff session is the middle
tier between "API cart" and "printable list"; product maps are the
compounding cross-trip memory. Seed rows for retailer_links are inserted at
runtime (idempotent, insert-missing-only) so new retailers ship with app
updates without clobbering admin edits — the migration only creates schema.
"""

import sqlalchemy as sa
from alembic import op

revision = "0042_retailer_handoff"
down_revision = "0041_device_keys"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "retailer_links",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("key", sa.String(length=40), nullable=False, unique=True),
        sa.Column("label", sa.String(length=80), nullable=False),
        sa.Column("domain", sa.String(length=120), nullable=False),
        sa.Column("search_template", sa.String(length=300), nullable=True),
        sa.Column("product_template", sa.String(length=300), nullable=True),
        sa.Column("encoder", sa.String(length=10), nullable=False, server_default="plus"),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="unverified"),
        sa.Column("last_verified", sa.DateTime(timezone=True), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )

    op.create_table(
        "retailer_handoff_sessions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("retailer_key", sa.String(length=40), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="active"),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
    )

    op.create_table(
        "retailer_handoff_items",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "session_id",
            sa.Uuid(),
            sa.ForeignKey("retailer_handoff_sessions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "shopping_list_item_id",
            sa.Uuid(),
            sa.ForeignKey("shopping_list_items.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("item_id", sa.Uuid(), sa.ForeignKey("items.id", ondelete="SET NULL"), nullable=True),
        sa.Column("name_snapshot", sa.String(length=200), nullable=False),
        sa.Column("quantity_snapshot", sa.Numeric(12, 3), nullable=False, server_default="1"),
        sa.Column("unit_snapshot", sa.String(length=24), nullable=False, server_default="each"),
        sa.Column("state", sa.String(length=16), nullable=False, server_default="pending"),
        sa.Column("external_id", sa.String(length=120), nullable=True),
        sa.Column("external_name", sa.String(length=200), nullable=True),
        sa.Column("external_url", sa.String(length=500), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
    )
    op.create_index("ix_handoff_items_session", "retailer_handoff_items", ["session_id"])

    op.create_table(
        "retailer_product_maps",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("item_id", sa.Uuid(), sa.ForeignKey("items.id", ondelete="CASCADE"), nullable=False),
        sa.Column("retailer_key", sa.String(length=40), nullable=False),
        sa.Column("external_id", sa.String(length=120), nullable=False),
        sa.Column("external_name", sa.String(length=200), nullable=True),
        sa.Column("external_url", sa.String(length=500), nullable=True),
        sa.Column("upc", sa.String(length=32), nullable=True),
        sa.Column("size", sa.String(length=80), nullable=True),
        sa.Column("source", sa.String(length=16), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False, server_default="1"),
        sa.Column("confirmed_count", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("last_confirmed_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("item_id", "retailer_key", "external_id", name="uq_retailer_map_identity"),
    )
    op.create_index("ix_retailer_maps_item_retailer", "retailer_product_maps", ["item_id", "retailer_key"])


def downgrade() -> None:
    op.drop_index("ix_retailer_maps_item_retailer", table_name="retailer_product_maps")
    op.drop_table("retailer_product_maps")
    op.drop_index("ix_handoff_items_session", table_name="retailer_handoff_items")
    op.drop_table("retailer_handoff_items")
    op.drop_table("retailer_handoff_sessions")
    op.drop_table("retailer_links")
