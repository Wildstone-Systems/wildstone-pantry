"""phase 6 purchase import

Revision ID: 0007_phase6_purchase_import
Revises: 0006_phase5_auth_licensing
Create Date: 2026-02-13

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0007_phase6_purchase_import"
down_revision: Union[str, Sequence[str], None] = "0006_phase5_auth_licensing"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "purchases",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("merchant", sa.String(length=120), nullable=True),
        sa.Column("source", sa.String(length=40), nullable=False, server_default="csv"),
        sa.Column("purchased_on", sa.Date(), nullable=True),
        sa.Column("location_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("status", sa.String(length=40), nullable=False, server_default="pending"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["location_id"], ["inventory_locations.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_purchases_location_id", "purchases", ["location_id"], unique=False)
    op.create_index("ix_purchases_purchased_on", "purchases", ["purchased_on"], unique=False)

    op.create_table(
        "purchase_line_items",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("purchase_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("raw_line", sa.Text(), nullable=False),
        sa.Column("parsed_name", sa.String(length=200), nullable=True),
        sa.Column("parsed_quantity", sa.Numeric(precision=12, scale=3), nullable=False, server_default="1"),
        sa.Column("parsed_unit", sa.String(length=24), nullable=False, server_default="each"),
        sa.Column("parsed_barcode", sa.String(length=64), nullable=True),
        sa.Column("matched_item_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("expiration_days_estimate", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["matched_item_id"], ["items.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["purchase_id"], ["purchases.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_purchase_line_items_purchase_id", "purchase_line_items", ["purchase_id"], unique=False)
    op.create_index("ix_purchase_line_items_matched_item_id", "purchase_line_items", ["matched_item_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_purchase_line_items_matched_item_id", table_name="purchase_line_items")
    op.drop_index("ix_purchase_line_items_purchase_id", table_name="purchase_line_items")
    op.drop_table("purchase_line_items")

    op.drop_index("ix_purchases_purchased_on", table_name="purchases")
    op.drop_index("ix_purchases_location_id", table_name="purchases")
    op.drop_table("purchases")

