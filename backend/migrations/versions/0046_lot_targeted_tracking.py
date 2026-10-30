"""inventory_transactions.lot_id / items.ignore_expiry — ADR 0023

Two columns, both nullable/defaulted so every existing row stays valid.

``lot_id`` records WHICH container a transaction acted on. Finishing a container
("this tub is empty") is a fact about one physical object, and undoing it has to
put the content back into that same lot — not into whatever FEFO would pick
today. Without the column the reversal can only guess, which is the failure this
ADR exists to remove. No FK: ``inv_lot`` is a projection that is deleted and
rebuilt wholesale on every replay.

``ignore_expiry`` marks items whose best-by is meaningless (spices, condiments).
They are suppressed from the expiring feed entirely — a list that nags about
paprika every three days teaches the household to ignore the list.
"""

import sqlalchemy as sa
from alembic import op

revision = "0046_lot_targeted_tracking"
down_revision = "0045_cook_event_servings_made"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("inventory_transactions", sa.Column("lot_id", sa.Uuid(), nullable=True))
    op.add_column(
        "items",
        sa.Column("ignore_expiry", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column("items", "ignore_expiry")
    op.drop_column("inventory_transactions", "lot_id")
