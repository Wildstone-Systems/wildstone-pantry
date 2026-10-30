"""ingredient_reference: seeded unit-conversion lookup (step 5 / ADR 0011 D2)

Revision ID: 0023_ingredient_reference
Revises: 0022_item_macros
Create Date: 2026-07-23

A CRUD-editable lookup of common-ingredient density (g/ml) and grams-per-each,
consulted by the unit-conversion service when an item hasn't set its own
override. Seeded with a small starting list (self-contained snapshot; the live
seed list is in ``modules.catalog.services.ingredient_reference.SEED_REFERENCE``
for the create_all test harness).
"""

import uuid

import sqlalchemy as sa
from alembic import op

from app.core.db_types import UUID

# revision identifiers
revision = "0023_ingredient_reference"
down_revision = "0022_item_macros"
branch_labels = None
depends_on = None

_SEED = [
    ("water", "liquid", "1.0", None),
    ("milk", "liquid", "1.03", None),
    ("vegetable oil", "oil", "0.92", None),
    ("olive oil", "oil", "0.91", None),
    ("canola oil", "oil", "0.92", None),
    ("butter", "fat", "0.96", None),
    ("honey", "sweetener", "1.42", None),
    ("maple syrup", "sweetener", "1.32", None),
    ("all purpose flour", "flour", "0.53", None),
    ("bread flour", "flour", "0.55", None),
    ("granulated sugar", "sweetener", "0.85", None),
    ("brown sugar", "sweetener", "0.90", None),
    ("salt", "seasoning", "1.22", None),
    ("rice", "grain", "0.85", None),
    ("egg", "produce", None, "50"),
    ("banana", "produce", None, "120"),
    ("apple", "produce", None, "182"),
    ("onion", "produce", None, "110"),
    ("garlic clove", "produce", None, "3"),
    ("lemon", "produce", None, "58"),
]


def upgrade() -> None:
    table = op.create_table(
        "ingredient_reference",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(200), nullable=False, unique=True),
        sa.Column("category", sa.String(100), nullable=True),
        sa.Column("density_g_per_ml", sa.Numeric(8, 4), nullable=True),
        sa.Column("grams_per_each", sa.Numeric(10, 2), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.bulk_insert(
        table,
        [
            {
                "id": uuid.uuid4(),
                "name": name,
                "category": category,
                "density_g_per_ml": density,
                "grams_per_each": grams_each,
                "notes": None,
            }
            for name, category, density, grams_each in _SEED
        ],
    )


def downgrade() -> None:
    op.drop_table("ingredient_reference")
