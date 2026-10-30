"""shopping list + item restock threshold (step 3: close the loop)

Revision ID: 0016_shopping_list_restock
Revises: 0015_user_is_provisioned
Create Date: 2026-07-22

Adds items.restock_threshold (min-stock level) and the household shopping_list_items
table (auto entries from threshold breaches + manual entries).
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

# revision identifiers
revision = "0016_shopping_list_restock"
down_revision = "0015_user_is_provisioned"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("items", sa.Column("restock_threshold", sa.Numeric(12, 3), nullable=True))

    op.create_table(
        "shopping_list_items",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("item_id", UUID(as_uuid=True), sa.ForeignKey("items.id", ondelete="CASCADE"), nullable=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("quantity", sa.Numeric(12, 3), nullable=False, server_default="1"),
        sa.Column("unit", sa.String(24), nullable=False, server_default="each"),
        sa.Column("source", sa.String(16), nullable=False, server_default="manual"),
        sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_shopping_list_items_item_id", "shopping_list_items", ["item_id"])


def downgrade() -> None:
    op.drop_index("ix_shopping_list_items_item_id", table_name="shopping_list_items")
    op.drop_table("shopping_list_items")
    op.drop_column("items", "restock_threshold")
