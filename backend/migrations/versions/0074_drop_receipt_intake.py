"""drop receipt intake tables (receipts, review drafts, CSV purchase claims)

Receipt text/photo ingestion, email receipts, IMAP purchase import and CSV
purchase import were removed; bulk intake now happens through the agent/MCP
layer. Purchase history (``purchases`` / ``purchase_line_items``) stays because
store hand-offs still write it. Downgrade recreates the tables empty.
"""

import sqlalchemy as sa
from alembic import op

from app.core.db_types import JSONB, UUID

revision = "0074_drop_receipt_intake"
down_revision = "0073_drop_pantryos_connect"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_table("purchase_csv_claims")
    op.drop_table("purchase_review_drafts")
    op.drop_table("receipt_review_drafts")
    op.drop_table("receipt_line_items")
    op.drop_table("receipts")


def downgrade() -> None:
    op.create_table(
        "receipts",
        sa.Column("id", UUID(), primary_key=True),
        sa.Column("source_filename", sa.String(length=255), nullable=True),
        sa.Column("raw_text", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=40), nullable=False, server_default="pending"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_table(
        "receipt_line_items",
        sa.Column("id", UUID(), primary_key=True),
        sa.Column("receipt_id", UUID(), sa.ForeignKey("receipts.id", ondelete="CASCADE"), nullable=False),
        sa.Column("raw_line", sa.Text(), nullable=False),
        sa.Column("parsed_name", sa.String(length=200), nullable=True),
        sa.Column("parsed_quantity", sa.Numeric(precision=12, scale=3), nullable=False, server_default="1"),
        sa.Column("parsed_unit", sa.String(length=24), nullable=False, server_default="each"),
        sa.Column("matched_item_id", UUID(), sa.ForeignKey("items.id", ondelete="SET NULL"), nullable=True),
        sa.Column("expiration_days_estimate", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_receipt_line_items_receipt_id", "receipt_line_items", ["receipt_id"])
    op.create_index("ix_receipt_line_items_matched_item_id", "receipt_line_items", ["matched_item_id"])
    for name in ("receipt_review_drafts", "purchase_review_drafts"):
        op.create_table(
            name,
            sa.Column("id", UUID(), primary_key=True),
            sa.Column("actor_id", UUID(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
            sa.Column("token_hash", sa.String(64), nullable=False),
            sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("revision", sa.Integer(), nullable=False),
            sa.Column("document", JSONB(), nullable=False),
        )
        op.create_index(f"ix_{name}_expires_at", name, ["expires_at"])
    op.create_table(
        "purchase_csv_claims",
        sa.Column("location_id", UUID(), sa.ForeignKey("inventory_locations.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("source_fingerprint", sa.String(64), primary_key=True),
        sa.Column("purchase_id", UUID(), sa.ForeignKey("purchases.id", ondelete="CASCADE"), nullable=True),
    )
