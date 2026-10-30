"""Phase 10: recipe import provenance columns

Revision ID: 0009_phase10_recipe_import
Revises: 0008_phase7_import_dedupe
Create Date: 2026-02-13
"""

from alembic import op
import sqlalchemy as sa


revision = "0009_phase10_recipe_import"
down_revision = "0008_phase7_import_dedupe"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("recipes", sa.Column("source", sa.String(length=32), nullable=True))
    op.add_column("recipes", sa.Column("source_filename", sa.String(length=256), nullable=True))
    op.add_column("recipes", sa.Column("source_text", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("recipes", "source_text")
    op.drop_column("recipes", "source_filename")
    op.drop_column("recipes", "source")

