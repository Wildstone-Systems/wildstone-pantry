"""Inventory event-sourcing ORM models (ADR 0002).

The legacy `inventory` / `inventory_transactions` tables still live in
`app.models` during the migration; these are the new event store + projections.
"""

from modules.inventory.models.event import EVENT_TYPES, InventoryEvent
from modules.inventory.models.projections import (
    InventoryBalance,
    InventoryLot,
    InventoryProjectionState,
)

__all__ = [
    "EVENT_TYPES",
    "InventoryEvent",
    "InventoryBalance",
    "InventoryLot",
    "InventoryProjectionState",
]
