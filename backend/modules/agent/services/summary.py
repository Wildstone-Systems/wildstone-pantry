"""Household summary for integrations (ADR 0011).

One cheap poll target for agents and smart-home integrations: instead of five
per-location calls, a single snapshot of the numbers an automation cares about.
Pure read-only composition through other modules' **service interfaces** —
this module owns no tables and touches no other module's models.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from sqlalchemy.orm import Session

from modules.inventory.services.inventory import count_active_locations, expiry_counts
from modules.shopping.services import shopping_list as shopping_service


def build_summary(db: Session, *, within_days: int = 7) -> dict[str, Any]:
    expiring_soon, expired = expiry_counts(db, within_days=within_days)

    # Shopping via the service so auto entries are reconciled on read, matching
    # what the list screen shows; low stock mirrors the auto-entry rule.
    entries = shopping_service.list_entries(db, refresh=True)
    shopping_pending = sum(1 for entry in entries if entry.get("status") == "pending")

    return {
        "generated_on": date.today(),
        "within_days": within_days,
        "locations": count_active_locations(db),
        "expiring_soon": expiring_soon,
        "expired": expired,
        "low_stock_items": shopping_service.count_items_below_threshold(db),
        "shopping_pending": shopping_pending,
    }
