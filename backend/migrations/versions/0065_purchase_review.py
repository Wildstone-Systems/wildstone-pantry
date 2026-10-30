"""Durable reviewed CSV drafts and nondestructive CSV source claims."""

import sqlalchemy as sa
from alembic import op

from app.core.db_types import JSONB, UUID

revision = "0065_purchase_review"
down_revision = "0064_receipt_review_drafts"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "purchase_review_drafts",
        sa.Column("id", UUID(), primary_key=True),
        sa.Column("actor_id", UUID(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("document", JSONB(), nullable=False),
    )
    op.create_index("ix_purchase_review_drafts_expires_at", "purchase_review_drafts", ["expires_at"])
    claims = op.create_table(
        "purchase_csv_claims",
        sa.Column("location_id", UUID(), sa.ForeignKey("inventory_locations.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("source_fingerprint", sa.String(64), primary_key=True),
        sa.Column("purchase_id", UUID(), sa.ForeignKey("purchases.id", ondelete="CASCADE"), nullable=True),
    )
    purchases = sa.table(
        "purchases",
        sa.column("id", UUID()),
        sa.column("location_id", UUID()),
        sa.column("source_fingerprint", sa.String(64)),
        sa.column("source", sa.String()),
        sa.column("created_at", sa.DateTime()),
    )
    ranked = (
        sa.select(
            purchases.c.id,
            purchases.c.location_id,
            purchases.c.source_fingerprint,
            sa.func.row_number()
            .over(
                partition_by=[purchases.c.location_id, purchases.c.source_fingerprint],
                order_by=[purchases.c.created_at, purchases.c.id],
            )
            .label("source_rank"),
        )
        .where(
            purchases.c.source == "csv",
            purchases.c.location_id.is_not(None),
            purchases.c.source_fingerprint.is_not(None),
        )
        .subquery()
    )
    op.get_bind().execute(
        sa.insert(claims).from_select(
            ["location_id", "source_fingerprint", "purchase_id"],
            sa.select(ranked.c.location_id, ranked.c.source_fingerprint, ranked.c.id).where(ranked.c.source_rank == 1),
        )
    )


def downgrade():
    op.drop_table("purchase_csv_claims")
    op.drop_table("purchase_review_drafts")
