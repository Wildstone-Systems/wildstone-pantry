"""Recipe source-provided per-serving nutrition override (issue #51)."""

import sqlalchemy as sa
from alembic import op

revision = "0072_recipe_nutrition_override"
down_revision = "0071_meal_plan_calendar_feeds"
branch_labels = None
depends_on = None

_NUMERIC = ("calories", "protein_g", "fat_g", "carbs_g")


def upgrade():
    with op.batch_alter_table("recipes") as batch:
        for name in _NUMERIC:
            batch.add_column(sa.Column(f"nutrition_override_{name}", sa.Numeric(9, 2), nullable=True))
        batch.add_column(sa.Column("nutrition_override_source", sa.String(200), nullable=True))


def downgrade():
    with op.batch_alter_table("recipes") as batch:
        batch.drop_column("nutrition_override_source")
        for name in reversed(_NUMERIC):
            batch.drop_column(f"nutrition_override_{name}")
