"""Preserve historical keys without guessing which were confirmed.

Revision ID: 0058_canonical_provenance
Revises: 0057_wildstone_list_fingerprint
"""
import sqlalchemy as sa
from alembic import op

revision = "0058_canonical_provenance"
down_revision = "0057_wildstone_list_fingerprint"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("items", sa.Column("canonical_provenance", sa.String(20), nullable=False, server_default="legacy_unknown"))
    op.add_column("items", sa.Column("canonical_version", sa.Integer(), nullable=True))
    op.add_column("items", sa.Column("canonical_revision", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("items", sa.Column("canonical_confirmed_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    for name in ("canonical_confirmed_at", "canonical_revision", "canonical_version", "canonical_provenance"):
        op.drop_column("items", name)
