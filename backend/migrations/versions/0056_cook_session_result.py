"""Cook sessions keep what their Finish reported.

Revision ID: 0056_cook_session_result
Revises: 0055_wildstone_relay_start

A Finish retried after a lost response answers with the cook already recorded;
keeping the original result lets it say the same thing the first time did
(the batch size used, the lines trimmed to what was on the shelf).
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0056_cook_session_result"
down_revision = "0055_wildstone_relay_start"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("cook_sessions", sa.Column("result", JSONB, nullable=True))


def downgrade() -> None:
    op.drop_column("cook_sessions", "result")
