"""Wildstone relay: remember the grocery list's last reported state.

Revision ID: 0057_wildstone_list_fingerprint
Revises: 0056_cook_session_result

``pantry.shopping_list_changed`` (ADR 0026 §4, #78) fires when the grocery
list settles into a state other than the one last reported. The relay keeps
that state's fingerprint; an existing relay starts with none and takes the
list as it stands as its starting picture on the next pass.
"""

import sqlalchemy as sa
from alembic import op

revision = "0057_wildstone_list_fingerprint"
down_revision = "0056_cook_session_result"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("wildstone_relay_state", sa.Column("list_fingerprint", sa.String(64), nullable=True))


def downgrade() -> None:
    op.drop_column("wildstone_relay_state", "list_fingerprint")
