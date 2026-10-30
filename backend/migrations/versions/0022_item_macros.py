"""item structured macros (per-100g) + unit-conversion hints (step 5 / ADR 0011 D1-D2)

Revision ID: 0022_item_macros
Revises: 0021_agent_keys
Create Date: 2026-07-23

Adds per-100g nutrition (energy/protein/fat/carbs) + an optional serving_size_g,
and per-item unit-conversion hints (density_g_per_ml, grams_per_each). All
nullable — a missing figure makes a rollup partial rather than wrong. Retains
items.calories_per_unit (step 4b) for back-compat.
"""

import sqlalchemy as sa
from alembic import op

# revision identifiers
revision = "0022_item_macros"
# Re-parented onto 0022_catalog_archive: both migrations were authored
# against 0021_agent_keys on parallel branches, which left Alembic with two
# heads and made `upgrade head` refuse to run. AGENTS.md requires a linear
# graph, so this chain follows the archive migration rather than forking.
down_revision = "0022_catalog_archive"
branch_labels = None
depends_on = None

_COLUMNS = [
    ("energy_kcal_100g", sa.Numeric(10, 2)),
    ("protein_g_100g", sa.Numeric(10, 2)),
    ("fat_g_100g", sa.Numeric(10, 2)),
    ("carbs_g_100g", sa.Numeric(10, 2)),
    ("serving_size_g", sa.Numeric(10, 2)),
    ("density_g_per_ml", sa.Numeric(8, 4)),
    ("grams_per_each", sa.Numeric(10, 2)),
]


def upgrade() -> None:
    for name, coltype in _COLUMNS:
        op.add_column("items", sa.Column(name, coltype, nullable=True))


def downgrade() -> None:
    for name, _ in reversed(_COLUMNS):
        op.drop_column("items", name)
