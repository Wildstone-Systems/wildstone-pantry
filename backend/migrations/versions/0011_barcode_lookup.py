"""add item barcode lookup fields

Revision ID: 0011_barcode_lookup
Revises: 0733c0811ad3
Create Date: 2026-04-14

Adds external barcode lookup support to the items table:
- barcode_type: UPC, EAN13, EAN8, ISBN
- image_url: product image from external provider
- size_description: human-readable size ("1 gallon", "16 oz")
- size_quantity: numeric quantity
- size_unit: unit string (gal, oz, lb, ml, l, ea)
- nutrition: JSONB nutrition facts from Open Food Facts
- source: where the item data came from (open_food_facts, kroger, go_upc, manual)
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

# revision identifiers
revision = "0011_barcode_lookup"
down_revision = "0733c0811ad3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("items", sa.Column("barcode_type", sa.String(10), nullable=False, server_default="UPC"))
    op.add_column("items", sa.Column("image_url", sa.String(500), nullable=True))
    op.add_column("items", sa.Column("size_description", sa.String(100), nullable=True))
    op.add_column("items", sa.Column("size_quantity", sa.Numeric(10, 2), nullable=True))
    op.add_column("items", sa.Column("size_unit", sa.String(20), nullable=True))
    op.add_column("items", sa.Column("nutrition", JSONB, nullable=True))
    op.add_column("items", sa.Column("source", sa.String(50), nullable=False, server_default="manual"))


def downgrade() -> None:
    op.drop_column("items", "source")
    op.drop_column("items", "nutrition")
    op.drop_column("items", "size_unit")
    op.drop_column("items", "size_quantity")
    op.drop_column("items", "size_description")
    op.drop_column("items", "image_url")
    op.drop_column("items", "barcode_type")