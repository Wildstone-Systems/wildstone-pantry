"""Retain source-linked human recipe import review receipts."""

import sqlalchemy as sa
from alembic import op

from app.core.db_types import JSONB

revision = "0066_recipe_import_review"
down_revision = "0065_purchase_review"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("recipes", sa.Column("import_review", JSONB(), nullable=True))


def downgrade():
    op.drop_column("recipes", "import_review")
