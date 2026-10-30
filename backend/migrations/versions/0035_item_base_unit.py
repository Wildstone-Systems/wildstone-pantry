"""items: base_unit (stored/consumed unit) — ADR 0016 Phase 1a

The unit an item's on-hand is tracked and decremented in, distinct from the unit
it's purchased in (a package). Backfilled deterministically from the size
dimension: mass → g, volume → ml, count/none → each. User-overridable later.
Foundation only — this migration changes no balances or behaviour.
"""

import sqlalchemy as sa
from alembic import op

from modules.catalog.services.units import default_base_unit

revision = "0035_item_base_unit"
down_revision = "0034_item_nutrition_source"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("items", sa.Column("base_unit", sa.String(16), nullable=True))
    bind = op.get_bind()
    rows = bind.execute(sa.text("SELECT id, stock_mode, size_unit FROM items")).fetchall()
    for row in rows:
        bind.execute(
            sa.text("UPDATE items SET base_unit = :b WHERE id = :i"),
            {"b": default_base_unit(row.stock_mode, row.size_unit), "i": row.id},
        )


def downgrade() -> None:
    op.drop_column("items", "base_unit")
