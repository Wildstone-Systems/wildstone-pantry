"""Wildstone relay: remember where the relay started.

Revision ID: 0055_wildstone_relay_start
Revises: 0054_cook_steps_timers

The relay re-reads the last few minutes of the ledger below its cursor for rows
that committed late (ADR 0026 §4). ``start_seq`` is the floor for that re-read:
ledger history from before PantryOS was connected is never news.
"""

import sqlalchemy as sa
from alembic import op

revision = "0055_wildstone_relay_start"
down_revision = "0054_cook_steps_timers"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "wildstone_relay_state",
        sa.Column("start_seq", sa.BigInteger(), nullable=False, server_default="0"),
    )
    # An existing relay has read up to its cursor: nothing below it is new.
    op.execute("UPDATE wildstone_relay_state SET start_seq = ledger_seq")


def downgrade() -> None:
    op.drop_column("wildstone_relay_state", "start_seq")
