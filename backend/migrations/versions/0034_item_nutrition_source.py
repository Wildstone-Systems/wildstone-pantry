"""items: nutrition_source (the USDA reference for an estimate)

Revision ID: 0034_item_nutrition_source
Revises: 0033_canonical_substitutions
Create Date: 2026-07-25

When macros are estimated by name from USDA, record which food they came from
(description + FDC id) so a wrong match ("sour cream" resolving to "heavy cream")
is auditable and correctable instead of silently imported.
"""

import sqlalchemy as sa
from alembic import op

revision = "0034_item_nutrition_source"
down_revision = "0033_canonical_substitutions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("items", sa.Column("nutrition_source", sa.String(300), nullable=True))


def downgrade() -> None:
    op.drop_column("items", "nutrition_source")
