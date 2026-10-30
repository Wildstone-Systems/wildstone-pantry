"""recipe_cook_events.servings_made / ingredient_overrides — ADR 0022 §6

The cook-along path records what was ACTUALLY cooked: the absolute servings
produced, and any per-line amount changes or omissions. Both nullable, so every
existing row (a legacy batch-multiplier cook) stays valid and readable, and the
existing `servings` / `substitutions` columns keep their meaning untouched.

A cook record that silently claims the full ingredient list when you left the
cilantro out is a lie the consumption trends would inherit.
"""

import sqlalchemy as sa
from alembic import op

from app.core.db_types import JSONB

revision = "0045_cook_event_servings_made"
down_revision = "0044_item_usda_fdc_id"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("recipe_cook_events", sa.Column("servings_made", sa.Numeric(12, 3), nullable=True))
    op.add_column("recipe_cook_events", sa.Column("ingredient_overrides", JSONB(), nullable=True))


def downgrade() -> None:
    op.drop_column("recipe_cook_events", "ingredient_overrides")
    op.drop_column("recipe_cook_events", "servings_made")
