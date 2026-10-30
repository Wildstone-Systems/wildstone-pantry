"""Explicit scoped substitution reviews and new global unit bases."""

import sqlalchemy as sa
from alembic import op

from app.core.db_types import JSONB, UUID

revision = "0067_scoped_substitutions"
down_revision = "0066_recipe_import_review"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("ingredient_substitutions", sa.Column("from_unit", sa.String(24), nullable=True))
    op.create_table(
        "substitution_reviews",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "session_id", UUID(as_uuid=True), sa.ForeignKey("cook_sessions.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("recipe_id", UUID(as_uuid=True), sa.ForeignKey("recipes.id", ondelete="CASCADE"), nullable=False),
        sa.Column(
            "ingredient_id",
            UUID(as_uuid=True),
            sa.ForeignKey("recipe_ingredients.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("from_item_id", UUID(as_uuid=True), sa.ForeignKey("items.id", ondelete="CASCADE"), nullable=False),
        sa.Column("to_item_id", UUID(as_uuid=True), sa.ForeignKey("items.id", ondelete="CASCADE"), nullable=False),
        sa.Column("context", JSONB(), nullable=False),
        sa.Column("snapshot", JSONB(), nullable=False),
        sa.Column("binding", sa.String(64), nullable=False),
        sa.Column("recipe_binding", sa.String(64), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("scope", sa.String(16), nullable=True),
        sa.Column("ratio", sa.Numeric(10, 4), nullable=True),
        sa.Column("from_unit", sa.String(24), nullable=False),
        sa.Column("unit", sa.String(24), nullable=True),
        sa.Column("evaluation", JSONB(), nullable=True),
        sa.Column("actor_id", sa.String(64), nullable=False),
        sa.Column("approved_by", sa.String(64), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    for name in ("session_id", "recipe_id", "ingredient_id"):
        op.create_index("ix_substitution_reviews_" + name, "substitution_reviews", [name])


def downgrade():
    # A basis-bound approval must never become an unbounded legacy approval.
    op.execute("UPDATE ingredient_substitutions SET approved = false WHERE from_unit IS NOT NULL")
    # Older cook code cannot interpret scoped IDs. Preserve the plan for history
    # but never let it finish as though the person chose the original ingredients.
    if op.get_bind().dialect.name == "postgresql":
        attached = "COALESCE(jsonb_array_length(plan -> 'substitution_approval_ids'), 0) > 0"
    else:
        attached = "COALESCE(json_array_length(plan, '$.substitution_approval_ids'), 0) > 0"
    op.execute(
        "UPDATE cook_sessions SET status = 'abandoned', ended_at = CURRENT_TIMESTAMP WHERE status = 'active' AND "
        + attached
    )
    op.drop_table("substitution_reviews")
    op.drop_column("ingredient_substitutions", "from_unit")
