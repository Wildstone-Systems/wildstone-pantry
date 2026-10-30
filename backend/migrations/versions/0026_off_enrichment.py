"""richer OpenFoodFacts capture + reconciliation provenance

Revision ID: 0026_off_enrichment
Revises: 0025_substitutions
Create Date: 2026-07-23

Adds more per-100g macro columns (fiber, sugars, saturated fat, sodium),
product-quality fields (ingredients_text, allergens, nutri_score, nova_group,
eco_score) captured from Open Food Facts on scan, and an
``off_last_checked_at`` timestamp recording the last time an item was
reconciled against an external provider. All nullable — a missing figure
just makes the catalog entry partial, not wrong.
"""

import sqlalchemy as sa
from alembic import op

from app.core.db_types import JSONB

# revision identifiers
revision = "0026_off_enrichment"
down_revision = "0025_substitutions"
branch_labels = None
depends_on = None

_COLUMNS = [
    ("fiber_g_100g", sa.Numeric(10, 2)),
    ("sugars_g_100g", sa.Numeric(10, 2)),
    ("saturated_fat_g_100g", sa.Numeric(10, 2)),
    ("sodium_g_100g", sa.Numeric(10, 2)),
    ("ingredients_text", sa.Text()),
    ("allergens", JSONB()),
    ("nutri_score", sa.String(1)),
    ("nova_group", sa.Integer()),
    ("eco_score", sa.String(1)),
    ("off_last_checked_at", sa.DateTime(timezone=True)),
]


def upgrade() -> None:
    for name, coltype in _COLUMNS:
        op.add_column("items", sa.Column(name, coltype, nullable=True))


def downgrade() -> None:
    for name, _ in reversed(_COLUMNS):
        op.drop_column("items", name)
