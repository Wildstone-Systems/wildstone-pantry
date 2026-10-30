"""Tests for Phase 3 event-driven architecture.

Grocy lesson: events should be explicit, testable, and not hidden triggers.
"""

import pytest
from decimal import Decimal
from uuid import uuid4

from core.events import get_event_bus, reset_event_bus
from core.events.base import (
    InventoryAdded,
    InventoryRemoved,
    InventoryAdjusted,
    InventoryMoved,
)
from handlers.audit_handler import AuditLogHandler


class InMemoryHandler:
    """Test handler that captures events for assertions."""
    
    def __init__(self):
        self.events = []
        self.handles_event_types = [
            "InventoryAdded",
            "InventoryRemoved",
            "InventoryAdjusted",
            "InventoryMoved",
        ]
    
    def handle(self, event, db=None):
        self.events.append(event)


@pytest.fixture
def event_bus():
    """Fresh event bus for each test."""
    bus = reset_event_bus()
    return bus


def test_event_bus_registers_and_publishes(event_bus):
    """Event bus registers handlers and publishes events."""
    handler = InMemoryHandler()
    event_bus.register(handler)
    
    event = InventoryAdded(
        item_id=uuid4(),
        location_id=uuid4(),
        quantity_added=Decimal("5.0"),
        quantity_after=Decimal("5.0"),
        unit="pieces",
        reason="test",
    )
    
    event_bus.publish(event)
    
    assert len(handler.events) == 1
    assert handler.events[0].event_type == "InventoryAdded"
    assert handler.events[0].quantity_added == Decimal("5.0")


def test_event_bus_filters_by_type(event_bus):
    """Handler only receives events it declared."""
    class OnlyAddedHandler(InMemoryHandler):
        def __init__(self):
            self.events = []
            self.handles_event_types = ["InventoryAdded"]
    
    handler_added = OnlyAddedHandler()
    handler_removed = InMemoryHandler()
    
    event_bus.register(handler_added)
    event_bus.register(handler_removed)
    
    added_event = InventoryAdded(
        item_id=uuid4(),
        location_id=uuid4(),
        quantity_added=Decimal("1.0"),
        quantity_after=Decimal("1.0"),
        unit="pieces",
    )
    removed_event = InventoryRemoved(
        item_id=uuid4(),
        location_id=uuid4(),
        quantity_removed=Decimal("1.0"),
        quantity_after=Decimal("0.0"),
        unit="pieces",
    )
    
    event_bus.publish(added_event)
    event_bus.publish(removed_event)
    
    assert len(handler_added.events) == 1
    assert handler_added.events[0].event_type == "InventoryAdded"
    
    assert len(handler_removed.events) == 2  # Gets both


def test_events_are_immutable():
    """Events cannot be modified after creation (frozen dataclass)."""
    event = InventoryAdded(
        item_id=uuid4(),
        location_id=uuid4(),
        quantity_added=Decimal("1.0"),
        quantity_after=Decimal("1.0"),
        unit="pieces",
    )
    
    # Frozen dataclasses reject attribute modification
    with pytest.raises((AttributeError, TypeError)):
        event.quantity_added = Decimal("999")


def test_audit_handler_logs_events(caplog):
    """Audit handler emits a structured log record for visibility (no print —
    AGENTS.md rule 4; mutations now route through this handler, ADR 0011)."""
    import logging

    handler = AuditLogHandler(log_to_console=True, log_to_db=False)

    event = InventoryAdded(
        item_id=uuid4(),
        location_id=uuid4(),
        quantity_added=Decimal("5"),
        quantity_after=Decimal("10"),
        unit="pieces",
    )

    with caplog.at_level(logging.INFO, logger="pantryos.audit"):
        handler.handle(event)

    assert any("InventoryAdded" in rec.getMessage() for rec in caplog.records)


def test_inventory_events_contain_all_context():
    """Events carry complete context for audit/debug."""
    item_id = uuid4()
    location_id = uuid4()
    
    added = InventoryAdded(
        item_id=item_id,
        location_id=location_id,
        quantity_added=Decimal("5"),
        quantity_after=Decimal("5"),
        unit="pieces",
        expiration_date=None,
        reason="received shipment",
        note="Arrived via FedEx",
    )
    
    assert added.item_id == item_id
    assert added.quantity_added == Decimal("5")
    assert added.reason == "received shipment"
    assert added.note == "Arrived via FedEx"
    
    # Test moved event with dual-state tracking
    source_id = uuid4()
    target_id = uuid4()
    
    moved = InventoryMoved(
        item_id=item_id,
        source_location_id=source_id,
        target_location_id=target_id,
        quantity_moved=Decimal("3"),
        source_quantity_after=Decimal("7"),
        target_quantity_after=Decimal("3"),
        unit="pieces",
        reason="restocking",
    )
    
    assert moved.source_quantity_after == Decimal("7")
    assert moved.target_quantity_after == Decimal("3")


def test_event_bus_is_singleton():
    """Global event bus returns consistent instance."""
    bus1 = get_event_bus()
    bus2 = get_event_bus()
    
    assert bus1 is bus2
    
    # Reset creates new instance
    bus3 = reset_event_bus()
    assert bus1 is not bus3
