"""phase 7 import dedupe and history

Revision ID: 0008_phase7_import_dedupe
Revises: 0007_phase6_purchase_import
Create Date: 2026-02-13

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "0008_phase7_import_dedupe"
down_revision: Union[str, Sequence[str], None] = "0007_phase6_purchase_import"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Nullable for backwards compatibility; enforce in service + partial unique index.
    op.add_column("purchases", sa.Column("source_fingerprint", sa.String(length=64), nullable=True))
    op.add_column("purchases", sa.Column("line_count", sa.Integer(), nullable=False, server_default="0"))

    op.create_index(
        "uq_purchases_location_fingerprint",
        "purchases",
        ["location_id", "source_fingerprint"],
        unique=True,
        postgresql_where=sa.text("source_fingerprint IS NOT NULL"),
    )
    op.create_index("ix_purchases_source_fingerprint", "purchases", ["source_fingerprint"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_purchases_source_fingerprint", table_name="purchases")
    op.drop_index("uq_purchases_location_fingerprint", table_name="purchases")

    op.drop_column("purchases", "line_count")
    op.drop_column("purchases", "source_fingerprint")

