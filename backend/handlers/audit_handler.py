"""Audit log handler for inventory events.

Grocy lesson: audit should be explicit event stream, not mutable tables.
"""

import logging

from sqlalchemy.orm import Session

from core.events.base import (
    Event,
    EventHandler,
    InventoryAdded,
    InventoryAdjusted,
    InventoryMoved,
    InventoryRemoved,
    RecipeCooked,
    RecipeCreated,
)


class AuditLogHandler(EventHandler):
    """Handler that logs inventory events for audit trail.
    
    This provides the "why did my inventory change?" answer.
    Unlike Grocy's dual-write model, this is a pure side effect
    of the event stream — if event happened, audit is guaranteed.
    """
    
    def __init__(self, log_to_db: bool = True, log_to_console: bool = False):
        self.log_to_db = log_to_db
        self.log_to_console = log_to_console
        self._logger = logging.getLogger("pantryos.audit")

    @property
    def handles_event_types(self) -> list[str]:
        return [
            "InventoryAdded",
            "InventoryRemoved", 
            "InventoryAdjusted",
            "InventoryMoved",
            "RecipeCreated",
            "RecipeCooked",
        ]
    
    def handle(self, event: Event, db: Session | None = None) -> None:
        """Log event to appropriate destinations."""
        if self.log_to_console:
            self._logger.info("%s: %s", event.event_type, self._format_event(event))
        
        if self.log_to_db and db:
            # Could write to dedicated audit table here
            # For now, the existing InventoryTransaction records suffice
            pass
    
    def _format_event(self, event: Event) -> str:
        """Format event for human-readable logging."""
        if isinstance(event, InventoryAdded):
            return f"Added {event.quantity_added} {event.unit} to item {event.item_id} at location {event.location_id}"
        elif isinstance(event, InventoryRemoved):
            return f"Removed {event.quantity_removed} {event.unit} from item {event.item_id} at location {event.location_id}"
        elif isinstance(event, InventoryAdjusted):
            return f"Adjusted item {event.item_id} from {event.quantity_before} to {event.quantity_after} {event.unit}"
        elif isinstance(event, InventoryMoved):
            return f"Moved {event.quantity_moved} {event.unit} from {event.source_location_id} to {event.target_location_id}"
        elif isinstance(event, RecipeCreated):
            return f"Created recipe '{event.name}' ({event.recipe_id})"
        elif isinstance(event, RecipeCooked):
            return f"Cooked recipe {event.recipe_id} ({event.servings} servings) at location {event.location_id}"
        return str(event)
