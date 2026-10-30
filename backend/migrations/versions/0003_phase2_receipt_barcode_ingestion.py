"""phase 2 receipt and barcode ingestion

Revision ID: 0003_phase2_receipt_barcode
Revises: 0002_phase1_inventory_engine
Create Date: 2026-02-13

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0003_phase2_receipt_barcode"
down_revision: Union[str, Sequence[str], None] = "0002_phase1_inventory_engine"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("receipts", sa.Column("status", sa.String(length=40), nullable=False, server_default="pending"))

    op.create_table(
        "barcode_aliases",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("barcode", sa.String(length=64), nullable=False),
        sa.Column("item_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["item_id"], ["items.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("barcode", name="uq_barcode_aliases_barcode"),
    )
    op.create_index("ix_barcode_aliases_item_id", "barcode_aliases", ["item_id"], unique=False)

    op.create_table(
        "receipt_line_items",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("receipt_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("raw_line", sa.Text(), nullable=False),
        sa.Column("parsed_name", sa.String(length=200), nullable=True),
        sa.Column("parsed_quantity", sa.Numeric(precision=12, scale=3), nullable=False, server_default="1"),
        sa.Column("parsed_unit", sa.String(length=24), nullable=False, server_default="each"),
        sa.Column("matched_item_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("expiration_days_estimate", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["matched_item_id"], ["items.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["receipt_id"], ["receipts.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_receipt_line_items_receipt_id", "receipt_line_items", ["receipt_id"], unique=False)
    op.create_index("ix_receipt_line_items_matched_item_id", "receipt_line_items", ["matched_item_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_receipt_line_items_matched_item_id", table_name="receipt_line_items")
    op.drop_index("ix_receipt_line_items_receipt_id", table_name="receipt_line_items")
    op.drop_table("receipt_line_items")

    op.drop_index("ix_barcode_aliases_item_id", table_name="barcode_aliases")
    op.drop_table("barcode_aliases")

    op.drop_column("receipts", "status")
