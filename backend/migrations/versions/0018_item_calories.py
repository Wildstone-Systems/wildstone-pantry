"""item calories per unit (step 4b: per-person calorie tracking)

Revision ID: 0018_item_calories
Revises: 0017_recipe_output
Create Date: 2026-07-22

Adds items.calories_per_unit (kcal per 1 unit of stock, nullable) — the
structured figure the plate/eat action snapshots onto each ``consumed`` event
so the per-profile daily rollup stays robust to later nutrition edits.

NOTE (merge coordination): down_revision points at 4a's ``0017_recipe_output``
so the chain stays linear once both step-4 branches merge. In this isolated
worktree 0017 is not present yet, so ``alembic upgrade`` /
``check_alembic_revisions.py`` will report a missing 0017 until the merge — the
in-process tests build the schema via ``Base.metadata.create_all`` and are
unaffected.
"""

import sqlalchemy as sa
from alembic import op

# revision identifiers
revision = "0018_item_calories"
down_revision = "0017_recipe_output"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("items", sa.Column("calories_per_unit", sa.Numeric(10, 2), nullable=True))


def downgrade() -> None:
    op.drop_column("items", "calories_per_unit")
