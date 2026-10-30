"""ingredient_substitutions: canonical keys + seed — ADR 0015 Phase 3

Re-key substitutions on the canonical ingredient (from_canonical → to_canonical)
so an approved swap generalises across brands without losing flavor. Existing
learned rows are backfilled from their items' canonical keys and marked approved;
item_ids become optional provenance. A small curated seed of common culinary subs
is inserted unapproved (suggestions until the user confirms one).
"""

import uuid

import sqlalchemy as sa
from alembic import op

from modules.catalog.services.canonical import canonical_ingredient

revision = "0033_canonical_substitutions"
down_revision = "0032_item_canonical_ingredient"
branch_labels = None
depends_on = None

# Curated, widely-accepted 1:1 subs. (from_label, to_label, ratio, outcome).
# Directional: approving never implies the reverse. Keys are derived with the SAME
# proposer the catalog uses, so they line up with recipe/item canonical keys.
_SEED = [
    ("sour cream", "plain greek yogurt", "1", "direct"),
    ("mayonnaise", "plain greek yogurt", "1", "acceptable"),
    ("buttermilk", "milk", "1", "emergency"),
    ("vegetable oil", "canola oil", "1", "direct"),
    ("canola oil", "vegetable oil", "1", "direct"),
    ("butter", "margarine", "1", "acceptable"),
    ("honey", "maple syrup", "1", "acceptable"),
    ("maple syrup", "honey", "1", "acceptable"),
]


def upgrade() -> None:
    op.add_column("ingredient_substitutions", sa.Column("from_canonical", sa.String(200), nullable=True))
    op.add_column("ingredient_substitutions", sa.Column("to_canonical", sa.String(200), nullable=True))
    op.add_column("ingredient_substitutions", sa.Column("from_label", sa.String(200), nullable=True))
    op.add_column("ingredient_substitutions", sa.Column("to_label", sa.String(200), nullable=True))
    op.add_column(
        "ingredient_substitutions",
        sa.Column("approved", sa.Boolean(), nullable=False, server_default=sa.true()),
    )
    op.create_index(
        "ix_ingredient_substitutions_from_canonical", "ingredient_substitutions", ["from_canonical"]
    )

    # item_ids become optional provenance.
    op.alter_column("ingredient_substitutions", "from_item_id", nullable=True)
    op.alter_column("ingredient_substitutions", "to_item_id", nullable=True)

    bind = op.get_bind()

    # Backfill existing (learned) rows from their items' canonical keys + names.
    rows = bind.execute(
        sa.text(
            """
            SELECT s.id,
                   fi.canonical_ingredient AS from_canon, fi.name AS from_name,
                   ti.canonical_ingredient AS to_canon,   ti.name AS to_name
            FROM ingredient_substitutions s
            JOIN items fi ON fi.id = s.from_item_id
            JOIN items ti ON ti.id = s.to_item_id
            """
        )
    ).fetchall()
    for r in rows:
        bind.execute(
            sa.text(
                "UPDATE ingredient_substitutions SET from_canonical=:fc, to_canonical=:tc, "
                "from_label=:fl, to_label=:tl, approved=true WHERE id=:i"
            ),
            {"fc": r.from_canon, "tc": r.to_canon, "fl": r.from_name, "tl": r.to_name, "i": r.id},
        )

    # Defensive: drop any duplicate (from_canonical,to_canonical) before the unique
    # constraint (two learned item-pairs could resolve to the same keys).
    bind.execute(
        sa.text(
            """
            DELETE FROM ingredient_substitutions a
            USING ingredient_substitutions b
            WHERE a.from_canonical IS NOT NULL
              AND a.from_canonical = b.from_canonical
              AND a.to_canonical = b.to_canonical
              AND a.ctid > b.ctid
            """
        )
    )

    op.create_unique_constraint(
        "uq_substitution_from_to_canonical",
        "ingredient_substitutions",
        ["from_canonical", "to_canonical"],
    )

    # Seed common subs (unapproved), skipping any the backfill already produced.
    for from_label, to_label, ratio, outcome in _SEED:
        fc = canonical_ingredient(from_label)
        tc = canonical_ingredient(to_label)
        if not fc or not tc:
            continue
        bind.execute(
            sa.text(
                """
                INSERT INTO ingredient_substitutions
                    (id, from_canonical, to_canonical, from_label, to_label, approved, ratio, unit, outcome)
                VALUES
                    (:id, :fc, :tc, :fl, :tl, false, :ratio, 'each', :outcome)
                ON CONFLICT ON CONSTRAINT uq_substitution_from_to_canonical DO NOTHING
                """
            ),
            {
                "id": uuid.uuid4(),
                "fc": fc,
                "tc": tc,
                "fl": from_label,
                "tl": to_label,
                "ratio": ratio,
                "outcome": outcome,
            },
        )


def downgrade() -> None:
    op.drop_constraint("uq_substitution_from_to_canonical", "ingredient_substitutions", type_="unique")
    # Seed rows have no item_ids and would violate the restored NOT NULL.
    op.execute("DELETE FROM ingredient_substitutions WHERE from_item_id IS NULL OR to_item_id IS NULL")
    op.alter_column("ingredient_substitutions", "from_item_id", nullable=False)
    op.alter_column("ingredient_substitutions", "to_item_id", nullable=False)
    op.drop_index("ix_ingredient_substitutions_from_canonical", table_name="ingredient_substitutions")
    op.drop_column("ingredient_substitutions", "approved")
    op.drop_column("ingredient_substitutions", "to_label")
    op.drop_column("ingredient_substitutions", "from_label")
    op.drop_column("ingredient_substitutions", "to_canonical")
    op.drop_column("ingredient_substitutions", "from_canonical")
