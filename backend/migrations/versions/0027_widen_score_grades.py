"""widen nutri_score / eco_score to hold Open Food Facts grade labels

Revision ID: 0027_widen_score_grades
Revises: 0026_off_enrichment
Create Date: 2026-07-23

0026 sized ``nutri_score`` and ``eco_score`` at ``String(1)`` on the assumption
they are single letters a-e. Open Food Facts also returns ``unknown`` and
``not-applicable`` for the many products it cannot grade, so an autocreate on
scan blew up with ``value too long for type character varying(1)`` and the scan
500'd. Widen both to hold those labels.
"""

import sqlalchemy as sa
from alembic import op

# revision identifiers
revision = "0027_widen_score_grades"
down_revision = "0026_off_enrichment"
branch_labels = None
depends_on = None

_GRADE_COLUMNS = ("nutri_score", "eco_score")


def upgrade() -> None:
    for name in _GRADE_COLUMNS:
        op.alter_column(
            "items",
            name,
            existing_type=sa.String(1),
            type_=sa.String(32),
            existing_nullable=True,
        )


def downgrade() -> None:
    # Truncate rather than fail: a value like 'unknown' cannot fit back into
    # String(1), and losing a grade label is preferable to a broken downgrade.
    for name in _GRADE_COLUMNS:
        op.execute(f"UPDATE items SET {name} = left({name}, 1) WHERE {name} IS NOT NULL")
        op.alter_column(
            "items",
            name,
            existing_type=sa.String(32),
            type_=sa.String(1),
            existing_nullable=True,
        )
