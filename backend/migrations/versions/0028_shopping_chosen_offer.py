"""shopping list: chosen-offer snapshot

Revision ID: 0028_shopping_chosen_offer
Revises: 0027_widen_score_grades
Create Date: 2026-07-24

A shopping entry can be resolved to a concrete vendor/product offer (ADR 0013).
We keep a snapshot of the chosen offer (provider, vendor, sku, size, and the
price *at selection time*) alongside the catalog link. JSONB so the offer shape
can evolve without a migration; the price is a snapshot, never live.
"""

import sqlalchemy as sa
from alembic import op

from app.core.db_types import JSONB

revision = "0028_shopping_chosen_offer"
down_revision = "0027_widen_score_grades"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("shopping_list_items", sa.Column("chosen_offer", JSONB(), nullable=True))


def downgrade() -> None:
    op.drop_column("shopping_list_items", "chosen_offer")
