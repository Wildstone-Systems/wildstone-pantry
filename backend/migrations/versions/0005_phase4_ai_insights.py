"""phase 4 ai insights

Revision ID: 0005_phase4_ai_insights
Revises: 0004_phase3_recipe_consumption
Create Date: 2026-02-13

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0005_phase4_ai_insights"
down_revision: Union[str, Sequence[str], None] = "0004_phase3_recipe_consumption"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "consumption_trends",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("item_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("location_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("avg_daily_usage", sa.Numeric(12, 3), nullable=False),
        sa.Column("days_to_depletion", sa.Numeric(12, 3), nullable=True),
        sa.Column("sampled_days", sa.Integer(), nullable=False, server_default="14"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["item_id"], ["items.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["location_id"], ["inventory_locations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_consumption_trends_item_id", "consumption_trends", ["item_id"], unique=False)
    op.create_index("ix_consumption_trends_location_id", "consumption_trends", ["location_id"], unique=False)

    op.create_table(
        "restock_suggestions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("item_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("location_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("priority", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("reason", sa.String(length=120), nullable=False),
        sa.Column("recommendation", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["item_id"], ["items.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["location_id"], ["inventory_locations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_restock_suggestions_item_id", "restock_suggestions", ["item_id"], unique=False)
    op.create_index("ix_restock_suggestions_location_id", "restock_suggestions", ["location_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_restock_suggestions_location_id", table_name="restock_suggestions")
    op.drop_index("ix_restock_suggestions_item_id", table_name="restock_suggestions")
    op.drop_table("restock_suggestions")

    op.drop_index("ix_consumption_trends_location_id", table_name="consumption_trends")
    op.drop_index("ix_consumption_trends_item_id", table_name="consumption_trends")
    op.drop_table("consumption_trends")
