"""Wildstone Home Lab integration: outbox, relay state, stock flags, member links (ADR 0026).

Revision ID: 0053_wildstone_integration
Revises: 0052_cook_sessions
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0053_wildstone_integration"
down_revision = "0052_cook_sessions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "wildstone_outbox",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("event_id", sa.String(length=64), nullable=False, unique=True),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("envelope", JSONB, nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="pending"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_wildstone_outbox_status_next", "wildstone_outbox", ["status", "next_attempt_at"])

    op.create_table(
        "wildstone_relay_state",
        sa.Column("name", sa.String(length=32), primary_key=True),
        sa.Column("ledger_seq", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("cook_cursor", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expiry_scan_on", sa.Date(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )

    op.create_table(
        "wildstone_stock_flags",
        sa.Column("item_id", sa.Uuid(), primary_key=True),
        sa.Column("low", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("crossings", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )

    op.create_table(
        "wildstone_member_links",
        sa.Column(
            "profile_id",
            sa.Uuid(),
            sa.ForeignKey("household_profiles.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("member", sa.String(length=128), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table("wildstone_member_links")
    op.drop_table("wildstone_stock_flags")
    op.drop_table("wildstone_relay_state")
    op.drop_index("ix_wildstone_outbox_status_next", table_name="wildstone_outbox")
    op.drop_table("wildstone_outbox")
