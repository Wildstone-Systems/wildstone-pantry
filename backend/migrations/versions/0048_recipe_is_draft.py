"""recipes.is_draft — ADR 0024 §1

An ad hoc cook session IS a recipe row, created up front and appended to as
the cook proceeds. The flag is what keeps a half-captured draft out of every
surface where finished recipes appear (the list, suggestions, the
reconciliation queue) until Finish either promotes it or deletes it.

Server default false: every existing recipe is, by definition, not a draft.
"""

import sqlalchemy as sa
from alembic import op

revision = "0048_recipe_is_draft"
down_revision = "0047_recipe_images"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "recipes",
        sa.Column("is_draft", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column("recipes", "is_draft")
