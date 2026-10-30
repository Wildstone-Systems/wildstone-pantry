"""recipe ingredients: optional flag + normalise units to the vocabulary — ADR 0017 Phase 2

Adds ``recipe_ingredients.optional`` (a to-taste/garnish line, excluded from the
can-make score and never auto-deducted) and normalises existing ``unit`` values to
the recipe unit vocabulary (cups→cup, tablespoon→tbsp, …). Tokens that aren't in
the vocabulary (e.g. "pinch", "can") are left as-is on purpose — they surface as
"can't tell" in availability and get flagged for the user to remap on next edit.
"""

import sqlalchemy as sa
from alembic import op

from modules.catalog.services.units import normalize_unit, to_recipe_unit

revision = "0036_recipe_optional_units"
down_revision = "0035_item_base_unit"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "recipe_ingredients",
        sa.Column("optional", sa.Boolean(), nullable=False, server_default=sa.false()),
    )

    # Normalise recognised aliases to canonical vocabulary tokens; leave unknowns.
    bind = op.get_bind()
    rows = bind.execute(sa.text("SELECT id, unit FROM recipe_ingredients")).fetchall()
    for row in rows:
        canonical = to_recipe_unit(row.unit)
        target = canonical if canonical is not None else normalize_unit(row.unit or "")
        if target and target != (row.unit or ""):
            bind.execute(
                sa.text("UPDATE recipe_ingredients SET unit = :u WHERE id = :id"),
                {"u": target, "id": row.id},
            )


def downgrade() -> None:
    op.drop_column("recipe_ingredients", "optional")
