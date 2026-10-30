"""Balance projection: fold events into per-(item, location) quantities."""

from __future__ import annotations

import uuid
from collections.abc import Iterable
from decimal import Decimal

from modules.inventory.projections.event_data import EventData, balance_delta

BalanceKey = tuple[uuid.UUID, uuid.UUID]


def apply_event(balances: dict[BalanceKey, Decimal], event: EventData) -> dict[BalanceKey, Decimal]:
    """Apply one event to the balances map in place, returning it."""
    for key, delta in balance_delta(event).items():
        balances[key] = balances.get(key, Decimal("0")) + delta
    return balances


def replay_balances(events: Iterable[EventData]) -> dict[BalanceKey, Decimal]:
    """Rebuild all balances from scratch by folding events in order."""
    balances: dict[BalanceKey, Decimal] = {}
    for event in events:
        apply_event(balances, event)
    return balances
