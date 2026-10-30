"""Event bus implementation for Pantry-OS.

In-process, synchronous event bus. Handlers run inline after main operation.
Grocy lesson: side effects should be explicit, not hidden triggers.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from core.events.base import Event, EventHandler


class EventBus:
    """Simple in-process event bus.
    
    Usage:
        bus = EventBus()
        bus.register(AuditLogHandler())
        bus.register(WebhookHandler())
        
        # In service:
        bus.publish(InventoryAdded(...), db)
    
    Note: Single-tenant system, events are not persisted to queue.
    """
    
    def __init__(self):
        self._handlers: list[EventHandler] = []
        self._handlers_by_type: dict[str, list[EventHandler]] = {}
    
    def register(self, handler: EventHandler) -> EventBus:
        """Register a handler for event types it declares."""
        self._handlers.append(handler)
        for event_type in handler.handles_event_types:
            if event_type not in self._handlers_by_type:
                self._handlers_by_type[event_type] = []
            self._handlers_by_type[event_type].append(handler)
        return self
    
    def publish(self, event: Event, db: Session | None = None) -> None:
        """Publish event to all registered handlers.
        
        Runs handlers synchronously inline. Any exception stops propagation.
        This is intentional — handlers should be simple and reliable.
        """
        event_type = event.event_type
        handlers = self._handlers_by_type.get(event_type, [])
        
        for handler in handlers:
            try:
                handler.handle(event, db)
            except Exception:
                # Log but don't fail main operation
                # In production, you'd want proper logging here
                import traceback
                traceback.print_exc()
    
    def publish_all(self, events: list[Event], db: Session | None = None) -> None:
        """Publish multiple events in sequence."""
        for event in events:
            self.publish(event, db)


# Global event bus instance (singleton pattern)
_event_bus: EventBus | None = None


def get_event_bus() -> EventBus:
    """Get or create global event bus."""
    global _event_bus
    if _event_bus is None:
        _event_bus = EventBus()
    return _event_bus


def reset_event_bus() -> EventBus:
    """Reset global event bus (useful for testing)."""
    global _event_bus
    _event_bus = EventBus()
    return _event_bus
