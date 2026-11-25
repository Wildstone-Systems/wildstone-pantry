"""item restock unit: count the restock threshold in packages or base units

Adds ``items.restock_unit`` ('base' | 'package'). Existing rows keep 'base', so
every current threshold keeps its meaning.
"""

import sqlalchemy as sa
from alembic import op

revision = "0075_item_restock_unit"
down_revision = "0074_drop_receipt_intake"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "items",
        sa.Column("restock_unit", sa.String(length=16), nullable=False, server_default="base"),
    )


def downgrade() -> None:
    op.drop_column("items", "restock_unit")
