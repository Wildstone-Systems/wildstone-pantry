"""recipes.external_source / external_id / external_url — stable upstream identity

An importer pulling a feed (TheMealDB id 52802, a scraped URL) had no way to say
"this is the same recipe I imported last week", so every rerun created a duplicate.
The (external_source, external_id) pair is unique, making re-import an UPDATE.

Both columns are nullable and NULLs do not collide under a UNIQUE constraint, so
hand-entered recipes are unaffected and any number of them coexist.
"""

import sqlalchemy as sa
from alembic import op

revision = "0043_recipe_external_identity"
down_revision = "0042_retailer_handoff"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("recipes", sa.Column("external_source", sa.String(length=40), nullable=True))
    op.add_column("recipes", sa.Column("external_id", sa.String(length=120), nullable=True))
    op.add_column("recipes", sa.Column("external_url", sa.String(length=500), nullable=True))
    op.create_unique_constraint(
        "uq_recipes_external_identity",
        "recipes",
        ["external_source", "external_id"],
    )


def downgrade() -> None:
    op.drop_constraint("uq_recipes_external_identity", "recipes", type_="unique")
    op.drop_column("recipes", "external_url")
    op.drop_column("recipes", "external_id")
    op.drop_column("recipes", "external_source")
