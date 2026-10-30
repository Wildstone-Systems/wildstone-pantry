"""Base event types for Pantry-OS.

Events are immutable, serializable, and represent domain facts.
Grocy lesson: events are source of truth, not derived afterthoughts.
"""

from __future__ import annotations

from abc import ABC
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, ClassVar
from uuid import UUID, uuid4


class Event:
    """Base class for all domain events.
    
    Events are facts that happened. They:
    - Are named in past tense (ItemAdded, InventoryMoved)
    - Contain all data needed to understand what happened
    - Are immutable (dataclass frozen=True)
    - Are serializable
    """
    
    _event_type: ClassVar[str] = ""
    
    @property
    def event_type(self) -> str:
        return self._event_type or self.__class__.__name__


@dataclass(frozen=True)
class InventoryAdded(Event):
    """Event: quantity was added to inventory at a location."""
    item_id: UUID
    location_id: UUID
    quantity_added: Decimal
    quantity_after: Decimal
    unit: str
    event_id: UUID = field(default_factory=uuid4)
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    expiration_date: datetime | None = None
    reason: str | None = None
    note: str | None = None


@dataclass(frozen=True)
class InventoryRemoved(Event):
    """Event: quantity was removed from inventory at a location."""
    item_id: UUID
    location_id: UUID
    quantity_removed: Decimal
    quantity_after: Decimal
    unit: str
    event_id: UUID = field(default_factory=uuid4)
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    reason: str | None = None
    note: str | None = None


@dataclass(frozen=True)
class InventoryAdjusted(Event):
    """Event: inventory quantity was adjusted to a specific value."""
    item_id: UUID
    location_id: UUID
    quantity_before: Decimal
    quantity_after: Decimal
    unit: str
    event_id: UUID = field(default_factory=uuid4)
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    expiration_date: datetime | None = None
    reason: str | None = None
    note: str | None = None


@dataclass(frozen=True)
class InventoryMoved(Event):
    """Event: inventory was moved between locations."""
    item_id: UUID
    source_location_id: UUID
    target_location_id: UUID
    quantity_moved: Decimal
    source_quantity_after: Decimal
    target_quantity_after: Decimal
    unit: str
    event_id: UUID = field(default_factory=uuid4)
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    reason: str | None = None
    note: str | None = None


@dataclass(frozen=True)
class RecipeCreated(Event):
    """Event: a recipe was created."""
    recipe_id: UUID
    name: str
    event_id: UUID = field(default_factory=uuid4)
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass(frozen=True)
class RecipeCooked(Event):
    """Event: a recipe was cooked."""
    recipe_id: UUID
    location_id: UUID
    servings: int
    leftover_servings: int
    note: str | None = None
    event_id: UUID = field(default_factory=uuid4)
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


class EventHandler(ABC):
    """Base class for event handlers.
    
    Handlers are side effects: audit logging, webhooks, AI suggestions.
    They receive events after the main transaction commits.
    """
    
    def handle(self, event, db=None) -> None:
        """Handle the event. Called synchronously by event bus."""
        raise NotImplementedError
    
    @property
    def handles_event_types(self) -> list[str]:
        """List of event types this handler handles."""
        return []
