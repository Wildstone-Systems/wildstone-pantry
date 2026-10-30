"""items: stock_mode (counted vs measured) — ADR 0014 Phase 1, step 1

Revision ID: 0030_item_stock_mode
Revises: 0029_nutrition_estimated
Create Date: 2026-07-24

How an item's on-hand is naturally tracked: 'counted' (whole units — cans, eggs)
or 'measured' (by amount — milk, oil; content tracked per container). A
display/behaviour default over the lot/container substrate, always user-
overridable. Backfilled by heuristic: weight/volume-sized items are measured.
"""

import sqlalchemy as sa
from alembic import op

revision = "0030_item_stock_mode"
down_revision = "0029_nutrition_estimated"
branch_labels = None
depends_on = None

# Package units that imply a measured item (mirrors units._MEASURED_UNITS).
_MEASURED_UNITS = ("g", "kg", "mg", "oz", "lb", "ml", "l", "tsp", "tbsp", "floz", "cup", "pint", "qt", "gal")


def upgrade() -> None:
    op.add_column("items", sa.Column("stock_mode", sa.String(16), nullable=False, server_default="counted"))
    units = ", ".join(f"'{u}'" for u in _MEASURED_UNITS)
    op.execute(f"UPDATE items SET stock_mode = 'measured' WHERE lower(trim(size_unit)) IN ({units})")


def downgrade() -> None:
    op.drop_column("items", "stock_mode")
