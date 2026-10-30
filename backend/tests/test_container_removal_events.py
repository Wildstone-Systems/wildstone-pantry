"""Container removals reach the event bus — ADR 0023 × ADR 0011.

The lot-targeted verbs (finish / consume-from-lot / discard) are a second write
path alongside ``remove_inventory``. If they append to the ledger without
publishing ``InventoryRemoved``, anything downstream of the bus — the Home
Assistant outbound webhook, any other handler — silently misses them, and
external state drifts for exactly the items this ADR made easiest to consume.
"""

from __future__ import annotations

import httpx
import pytest

from core.events import reset_event_bus


class CapturingHandler:
    def __init__(self) -> None:
        self.events: list = []
        self.handles_event_types = ["InventoryRemoved"]

    def handle(self, event, db=None) -> None:  # noqa: ARG002 - bus calls with db
        self.events.append(event)


@pytest.fixture
def removals():
    bus = reset_event_bus()
    handler = CapturingHandler()
    bus.register(handler)
    yield handler
    reset_event_bus()


def _headers(client: httpx.Client) -> dict[str, str]:
    client.post("/api/auth/bootstrap-admin", json={"email": "admin@pantryos.local", "password": "TestPass123!"})
    login = client.post("/api/auth/login", json={"email": "admin@pantryos.local", "password": "TestPass123!"})
    return {"Authorization": f"Bearer {login.json()['token']}"}


def _setup(client, h, barcode="0300000000001", count=2):
    item = client.post("/api/items", json={"name": "Yogurt", "barcode": barcode}, headers=h).json()["id"]
    loc = client.post("/api/locations", json={"name": "Fridge"}, headers=h).json()["id"]
    client.post(
        "/api/inventory/add",
        json={
            "item_id": item,
            "location_id": loc,
            "quantity": count,
            "sealed_count": count,
            "container_size": 1,
            "container_unit": "qt",
        },
        headers=h,
    )
    lots = client.get(f"/api/inventory/lots?item_id={item}&location_id={loc}", headers=h).json()["lots"]
    return item, loc, lots[0]["lot_id"]


def test_finishing_a_container_publishes_a_removal(client: httpx.Client, removals) -> None:
    h = _headers(client)
    item, loc, lot_id = _setup(client, h)

    resp = client.post(f"/api/inventory/lots/{lot_id}/finish", headers=h)
    assert resp.status_code == 200, resp.text

    assert len(removals.events) == 1
    event = removals.events[0]
    assert str(event.item_id) == item
    assert str(event.location_id) == loc
    assert event.quantity_removed > 0
    # The balance as it now stands, so a subscriber can mirror it directly.
    assert event.quantity_after == event.quantity_removed  # one of two tubs left


def test_discarding_a_container_publishes_a_removal(client: httpx.Client, removals) -> None:
    h = _headers(client)
    _item, _loc, lot_id = _setup(client, h, barcode="0300000000002")

    resp = client.post(f"/api/inventory/lots/{lot_id}/spoil", headers=h)
    assert resp.status_code == 200, resp.text

    assert len(removals.events) == 1
    assert removals.events[0].reason == "spoiled"


def test_scanning_a_container_out_publishes_a_removal(client: httpx.Client, removals) -> None:
    h = _headers(client)
    _setup(client, h, barcode="0300000000003")

    resp = client.post(
        "/api/inventory/scan",
        json={"barcode": "0300000000003", "direction": "out", "quantity": 1},
        headers=h,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "done"

    assert len(removals.events) == 1


def test_an_already_empty_container_publishes_nothing(client: httpx.Client, removals) -> None:
    """A no-op moved no stock, so there is nothing to announce."""
    h = _headers(client)
    _item, _loc, lot_id = _setup(client, h, barcode="0300000000004", count=1)

    client.post(f"/api/inventory/lots/{lot_id}/finish", headers=h)
    assert len(removals.events) == 1

    client.post(f"/api/inventory/lots/{lot_id}/finish", headers=h)
    assert len(removals.events) == 1  # unchanged


def test_a_prompt_publishes_nothing(client: httpx.Client, removals) -> None:
    """Two open tubs means the scan asks and moves nothing — so no removal."""
    h = _headers(client)
    _item, _loc, lot_id = _setup(client, h, barcode="0300000000005", count=3)
    client.post(f"/api/inventory/lots/{lot_id}/open", headers=h)
    client.post(f"/api/inventory/lots/{lot_id}/open", headers=h)

    resp = client.post(
        "/api/inventory/scan",
        json={"barcode": "0300000000005", "direction": "out", "quantity": 1},
        headers=h,
    )
    assert resp.json()["status"] == "needs_container"
    assert removals.events == []
