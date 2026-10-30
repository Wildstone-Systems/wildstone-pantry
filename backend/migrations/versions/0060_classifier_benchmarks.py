"""Synthetic benchmark review/run records, independent of diagnostic cache.

Revision ID: 0060_classifier_benchmarks
Revises: 0059_classifier_evaluations
"""

import sqlalchemy as sa
from alembic import op

from app.core.db_types import JSONB, UUID

revision = "0060_classifier_benchmarks"
down_revision = "0059_classifier_evaluations"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "classifier_benchmarks",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("document", JSONB(), nullable=False),
    )
    op.create_index("ix_classifier_benchmarks_expires_at", "classifier_benchmarks", ["expires_at"])


def downgrade():
    op.drop_index("ix_classifier_benchmarks_expires_at", table_name="classifier_benchmarks")
    op.drop_table("classifier_benchmarks")
