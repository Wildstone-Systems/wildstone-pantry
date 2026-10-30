"""Bounded classifier shadow result retention; no request text.

Revision ID: 0059_classifier_evaluations
Revises: 0058_canonical_provenance
"""

import sqlalchemy as sa
from alembic import op

from app.core.db_types import JSONB, UUID

revision = "0059_classifier_evaluations"
down_revision = "0058_canonical_provenance"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "classifier_evaluations",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("result", JSONB(), nullable=False),
    )
    op.create_index("ix_classifier_evaluations_expires_at", "classifier_evaluations", ["expires_at"])


def downgrade():
    op.drop_index("ix_classifier_evaluations_expires_at", table_name="classifier_evaluations")
    op.drop_table("classifier_evaluations")
