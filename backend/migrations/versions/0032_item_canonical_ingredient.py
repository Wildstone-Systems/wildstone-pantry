"""items: canonical_ingredient key — ADR 0015 Phase 1

Revision ID: 0032_item_canonical_ingredient
Revises: 0031_lot_container_fields
Create Date: 2026-07-25

A normalised, brand/size-stripped match key so name-divergent records for the same
ingredient ("cumin" / "Lawry's Ground Cumin") resolve to one thing. Backfilled for
every existing item via the (pure, app-import-free) proposer so recipe availability
counts stock immediately, before anything is re-scanned.
"""

import sqlalchemy as sa
from alembic import op

# The proposer is deliberately dependency-free (no ORM/app imports), so importing
# it here does not pull the app graph into the migration.
from modules.catalog.services.canonical import canonical_ingredient

revision = "0032_item_canonical_ingredient"
down_revision = "0031_lot_container_fields"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("items", sa.Column("canonical_ingredient", sa.String(200), nullable=True))
    op.create_index("ix_items_canonical_ingredient", "items", ["canonical_ingredient"])

    # Backfill from each item's name (+ brand). Done in Python because the key
    # logic — brand list, size-token and flavor handling — can't be expressed in
    # portable SQL.
    bind = op.get_bind()
    items = bind.execute(sa.text("SELECT id, name, brand FROM items")).fetchall()
    for row in items:
        key = canonical_ingredient(row.name, row.brand)
        if key is not None:
            bind.execute(
                sa.text("UPDATE items SET canonical_ingredient = :k WHERE id = :i"),
                {"k": key, "i": row.id},
            )


def downgrade() -> None:
    op.drop_index("ix_items_canonical_ingredient", table_name="items")
    op.drop_column("items", "canonical_ingredient")
