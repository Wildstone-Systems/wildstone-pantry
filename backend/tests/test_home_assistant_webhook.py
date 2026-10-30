"""Outbound Home Assistant webhook (ADR 0011).

Covers the connector-config resolution (ADR 0009 layer), payload signing, the
publish path from a real inventory mutation, and the admin test endpoint.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from decimal import Decimal
from uuid import uuid4

import httpx
import pytest

from core.events.base import InventoryAdded
from core.events.bus import reset_event_bus
from handlers.registration import _home_assistant_config
from handlers.webhook_handler import (
    SCHEMA_VERSION,
    SIGNATURE_HEADER,
    TIMESTAMP_HEADER,
    WebhookHandler,
)


@pytest.fixture(autouse=True)
def _clean_bus():
    reset_event_bus()
    yield
    reset_event_bus()


def _admin_headers(client: httpx.Client) -> dict[str, str]:
    client.post("/api/auth/bootstrap-admin", json={"email": "admin@pantryos.local", "password": "TestPass123!"})
    login = client.post("/api/auth/login", json={"email": "admin@pantryos.local", "password": "TestPass123!"})
    return {"Authorization": f"Bearer {login.json()['token']}"}


class _CapturePost:
    def __init__(self):
        self.calls: list[dict] = []

    def __call__(self, url, data=None, headers=None, timeout=None):
        self.calls.append({"url": url, "body": data, "headers": headers or {}})

        class _Resp:
            def raise_for_status(self):
                pass

        return _Resp()


def _sample_event() -> InventoryAdded:
    return InventoryAdded(
        item_id=uuid4(),
        location_id=uuid4(),
        quantity_added=Decimal("2"),
        quantity_after=Decimal("5"),
        unit="each",
        reason="manual_add",
    )


def test_handler_posts_signed_versioned_payload(monkeypatch) -> None:
    capture = _CapturePost()
    monkeypatch.setattr("handlers.webhook_handler.requests.post", capture)

    handler = WebhookHandler(
        config_resolver=lambda db: {"webhook_url": "http://ha.local/api/webhook/x", "secret": "s3cret"},
        deliver_async=False,
    )
    handler.handle(_sample_event(), db=object())

    assert len(capture.calls) == 1
    call = capture.calls[0]
    assert call["url"] == "http://ha.local/api/webhook/x"

    payload = json.loads(call["body"])
    assert payload["schema_version"] == SCHEMA_VERSION
    assert payload["event_type"] == "InventoryAdded"
    assert payload["quantity_added"] == "2"
    assert payload["quantity_after"] == "5"

    # Signature = HMAC-SHA256("<timestamp>.<body>") — recomputable by the receiver.
    timestamp = call["headers"][TIMESTAMP_HEADER]
    expected = hmac.new(b"s3cret", timestamp.encode() + b"." + call["body"], hashlib.sha256).hexdigest()
    assert call["headers"][SIGNATURE_HEADER] == expected


def test_handler_unsigned_without_secret(monkeypatch) -> None:
    capture = _CapturePost()
    monkeypatch.setattr("handlers.webhook_handler.requests.post", capture)

    handler = WebhookHandler(
        config_resolver=lambda db: {"webhook_url": "http://ha.local/hook", "secret": ""},
        deliver_async=False,
    )
    handler.handle(_sample_event(), db=object())
    headers = capture.calls[0]["headers"]
    assert SIGNATURE_HEADER not in headers


def test_handler_skips_when_unconfigured(monkeypatch) -> None:
    capture = _CapturePost()
    monkeypatch.setattr("handlers.webhook_handler.requests.post", capture)

    handler = WebhookHandler(config_resolver=lambda db: {"webhook_url": "", "secret": ""}, deliver_async=False)
    handler.handle(_sample_event(), db=object())
    assert capture.calls == []


def test_inventory_add_fires_configured_webhook(client: httpx.Client, monkeypatch) -> None:
    """End-to-end: config saved in-app → a real /api/inventory/add publish hits
    the webhook, no restart. Mirrors the lifespan wiring with sync delivery."""
    capture = _CapturePost()
    monkeypatch.setattr("handlers.webhook_handler.requests.post", capture)
    reset_event_bus().register(
        WebhookHandler(config_resolver=_home_assistant_config, deliver_async=False)
    )

    h = _admin_headers(client)
    resp = client.put(
        "/api/admin/integrations/home_assistant",
        json={"updates": {"webhook_url": "http://ha.local/api/webhook/pantryos", "secret": "shared"}},
        headers=h,
    )
    assert resp.status_code == 200
    assert resp.json()["secret_set"]["secret"] is True
    assert "shared" not in resp.text  # secret never echoed back

    item_id = client.post("/api/items", json={"name": "HA Milk"}, headers=h).json()["id"]
    location_id = client.post("/api/locations", json={"name": "HA Fridge"}, headers=h).json()["id"]
    add = client.post(
        "/api/inventory/add",
        json={"item_id": item_id, "location_id": location_id, "quantity": 3, "unit": "each"},
        headers=h,
    )
    assert add.status_code == 200

    assert len(capture.calls) == 1
    payload = json.loads(capture.calls[0]["body"])
    assert payload["event_type"] == "InventoryAdded"
    assert payload["item_id"] == item_id
    assert payload["location_id"] == location_id
    assert Decimal(payload["quantity_after"]) == Decimal("3")
    assert TIMESTAMP_HEADER in capture.calls[0]["headers"]


def test_admin_test_endpoint_sends_ping(client: httpx.Client, monkeypatch) -> None:
    capture = _CapturePost()
    monkeypatch.setattr("handlers.webhook_handler.requests.post", capture)

    h = _admin_headers(client)

    # Unconfigured → a clean failure, not a 500.
    resp = client.post("/api/admin/integrations/home_assistant/test", headers=h)
    assert resp.status_code == 200
    assert resp.json()["ok"] is False

    client.put(
        "/api/admin/integrations/home_assistant",
        json={"updates": {"webhook_url": "http://ha.local/api/webhook/pantryos"}},
        headers=h,
    )
    resp = client.post("/api/admin/integrations/home_assistant/test", headers=h)
    assert resp.json()["ok"] is True
    payload = json.loads(capture.calls[0]["body"])
    assert payload == {"schema_version": SCHEMA_VERSION, "event_type": "pantryos.test"}


def test_deliveries_are_recorded_and_listed(client: httpx.Client, monkeypatch) -> None:
    """Observability: every outbound attempt lands in webhook_events and is
    readable (newest first) from the admin deliveries endpoint."""
    capture = _CapturePost()
    monkeypatch.setattr("handlers.webhook_handler.requests.post", capture)
    reset_event_bus().register(
        WebhookHandler(config_resolver=_home_assistant_config, deliver_async=False)
    )

    h = _admin_headers(client)
    client.put(
        "/api/admin/integrations/home_assistant",
        json={"updates": {"webhook_url": "http://ha.local/api/webhook/pantryos"}},
        headers=h,
    )

    # Nothing delivered yet.
    assert client.get("/api/admin/integrations/home_assistant/deliveries", headers=h).json() == []

    # A test ping and a real inventory event both record deliveries.
    client.post("/api/admin/integrations/home_assistant/test", headers=h)
    item_id = client.post("/api/items", json={"name": "HA Cheese"}, headers=h).json()["id"]
    location_id = client.post("/api/locations", json={"name": "HA Cellar"}, headers=h).json()["id"]
    client.post(
        "/api/inventory/add",
        json={"item_id": item_id, "location_id": location_id, "quantity": 1, "unit": "each"},
        headers=h,
    )

    rows = client.get("/api/admin/integrations/home_assistant/deliveries", headers=h).json()
    assert [r["event_type"] for r in rows] == ["InventoryAdded", "pantryos.test"]
    assert all(r["status"] == "delivered" for r in rows)
    assert all(r["delivered_at"] is not None for r in rows)


def test_failed_delivery_is_recorded(client: httpx.Client, monkeypatch) -> None:
    import requests as requests_lib

    def _boom(*args, **kwargs):
        raise requests_lib.ConnectionError("receiver down")

    monkeypatch.setattr("handlers.webhook_handler.requests.post", _boom)
    monkeypatch.setattr("handlers.webhook_handler.time.sleep", lambda s: None)
    reset_event_bus().register(
        WebhookHandler(config_resolver=_home_assistant_config, deliver_async=False)
    )

    h = _admin_headers(client)
    client.put(
        "/api/admin/integrations/home_assistant",
        json={"updates": {"webhook_url": "http://ha.local/api/webhook/pantryos"}},
        headers=h,
    )
    item_id = client.post("/api/items", json={"name": "HA Butter"}, headers=h).json()["id"]
    location_id = client.post("/api/locations", json={"name": "HA Fridge 2"}, headers=h).json()["id"]
    add = client.post(
        "/api/inventory/add",
        json={"item_id": item_id, "location_id": location_id, "quantity": 1, "unit": "each"},
        headers=h,
    )
    assert add.status_code == 200  # delivery failure never fails the operation

    rows = client.get("/api/admin/integrations/home_assistant/deliveries", headers=h).json()
    assert rows[0]["event_type"] == "InventoryAdded"
    assert rows[0]["status"] == "failed"
    assert rows[0]["attempts"] == 3
    assert rows[0]["delivered_at"] is None


def test_deliveries_endpoint_scoping(client: httpx.Client) -> None:
    h = _admin_headers(client)
    assert client.get("/api/admin/integrations/home_assistant/deliveries").status_code == 401
    assert client.get("/api/admin/integrations/imap/deliveries", headers=h).status_code == 404
