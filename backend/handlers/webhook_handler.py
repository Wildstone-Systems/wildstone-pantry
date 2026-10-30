"""Webhook handler for inventory events (ADR 0011).

Enables smart home integrations: Home Assistant, OpenHAB, etc. The target URL
and signing secret are resolved **at publish time** through the ADR 0009
connector-config layer (key ``home_assistant``), so an admin can point PantryOS
at an HA webhook from the Integrations screen with no restart. Payloads are
versioned (``schema_version``) and HMAC-SHA256-signed when a secret is set, and
delivery happens off-thread with a couple of retries so a slow receiver never
stalls an inventory write.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import threading
import time
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

import requests
from sqlalchemy.orm import Session

from core.events.base import (
    Event,
    EventHandler,
    InventoryAdded,
    InventoryAdjusted,
    InventoryMoved,
    InventoryRemoved,
)

SCHEMA_VERSION = 1
DELIVERY_ATTEMPTS = 3
SIGNATURE_HEADER = "X-PantryOS-Signature"
TIMESTAMP_HEADER = "X-PantryOS-Timestamp"

# Resolves the effective webhook config ({"webhook_url": ..., "secret": ...})
# for the current request's DB session.
ConfigResolver = Callable[[Session], dict[str, str]]


def _encode(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()


def _signature_headers(body: bytes, secret: str) -> dict[str, str]:
    """HMAC-SHA256 over ``"<timestamp>.<body>"``."""
    timestamp = str(int(time.time()))
    digest = hmac.new(secret.encode(), timestamp.encode() + b"." + body, hashlib.sha256).hexdigest()
    return {SIGNATURE_HEADER: digest, TIMESTAMP_HEADER: timestamp}


def deliver(
    url: str,
    payload: dict[str, Any],
    *,
    secret: str = "",
    timeout: float = 5.0,
    attempts: int = DELIVERY_ATTEMPTS,
) -> int:
    """POST a signed event payload, retrying transient failures. Returns the
    number of attempts used; raises on final failure so callers who care (the
    /test endpoint) see the reason."""
    body = _encode(payload)
    headers = {"Content-Type": "application/json"}
    if secret:
        headers.update(_signature_headers(body, secret))

    last_exc: Exception | None = None
    for attempt in range(attempts):
        try:
            resp = requests.post(url, data=body, headers=headers, timeout=timeout)
            resp.raise_for_status()
            return attempt + 1
        except requests.RequestException as exc:
            last_exc = exc
            if attempt < attempts - 1:
                time.sleep(0.5 * (attempt + 1))
    assert last_exc is not None
    raise last_exc


def record_delivery(payload: dict[str, Any], *, status: str, attempts: int) -> None:
    """Persist a delivery outcome to ``webhook_events`` ("has this ever fired,
    and when did it last work?" — the Integrations card reads it back).

    Opens its own session: delivery runs on a background thread where the
    request's session is long gone. Best-effort — an observability write must
    never break (or outlive) the delivery path it observes.
    """
    from uuid import UUID, uuid4

    from app.db import SessionLocal
    from app.models import WebhookEvent

    try:
        resource_id = UUID(str(payload.get("event_id")))
    except (ValueError, TypeError):
        resource_id = uuid4()  # test pings carry no event id

    now = datetime.now(UTC)
    try:
        db = SessionLocal()
        try:
            db.add(
                WebhookEvent(
                    event_type=str(payload.get("event_type", "unknown")),
                    resource_id=resource_id,
                    status=status,
                    attempts=attempts,
                    last_attempt_at=now,
                    delivered_at=now if status == "delivered" else None,
                )
            )
            db.commit()
        finally:
            db.close()
    except Exception:  # noqa: BLE001 - observability is strictly best-effort
        logging.getLogger(__name__).warning("Failed to record webhook delivery", exc_info=True)


def send_test_event(config: dict[str, str], *, timeout: float = 5.0) -> None:
    """Fire a ``pantryos.test`` event at the configured URL (Integrations
    "Send test event" button). Raises with the real reason on failure."""
    url = (config.get("webhook_url") or "").strip()
    if not url:
        raise ValueError("No webhook URL configured")
    payload = {"schema_version": SCHEMA_VERSION, "event_type": "pantryos.test"}
    try:
        attempts = deliver(url, payload, secret=config.get("secret", ""), timeout=timeout, attempts=1)
    except requests.RequestException:
        record_delivery(payload, status="failed", attempts=1)
        raise
    record_delivery(payload, status="delivered", attempts=attempts)


class WebhookHandler(EventHandler):
    """Emits inventory events to the configured outbound webhook.

    Usage:
        handler = WebhookHandler(config_resolver=resolve_home_assistant_config)
        bus.register(handler)
    """

    def __init__(
        self,
        config_resolver: ConfigResolver | None = None,
        webhook_url: str | None = None,
        timeout: float = 5.0,
        deliver_async: bool = True,
    ):
        self.config_resolver = config_resolver
        self.webhook_url = webhook_url  # static fallback when no resolver is wired
        self.timeout = timeout
        self.deliver_async = deliver_async

    @property
    def handles_event_types(self) -> list[str]:
        return [
            "InventoryAdded",
            "InventoryRemoved",
            "InventoryAdjusted",
            "InventoryMoved",
        ]

    def _resolve(self, db: Session | None) -> tuple[str, str]:
        if self.config_resolver is not None and db is not None:
            config = self.config_resolver(db)
            return (config.get("webhook_url") or "").strip(), config.get("secret", "")
        return (self.webhook_url or "").strip(), ""

    def handle(self, event: Event, db: Session | None = None) -> None:
        """Emit event to the webhook if one is configured."""
        url, secret = self._resolve(db)
        if not url:
            return

        payload = self._event_to_payload(event)

        def _send() -> None:
            try:
                attempts = deliver(url, payload, secret=secret, timeout=self.timeout)
            except requests.RequestException:
                # Webhooks are best-effort; never fail the inventory operation
                record_delivery(payload, status="failed", attempts=DELIVERY_ATTEMPTS)
            else:
                record_delivery(payload, status="delivered", attempts=attempts)

        if self.deliver_async:
            threading.Thread(target=_send, name="pantryos-webhook", daemon=True).start()
        else:
            _send()

    def _event_to_payload(self, event: Event) -> dict[str, Any]:
        """Convert event to webhook payload."""
        base = {
            "schema_version": SCHEMA_VERSION,
            "event_type": event.event_type,
            "timestamp": event.timestamp.isoformat() if hasattr(event, "timestamp") else None,
            "event_id": str(event.event_id) if hasattr(event, "event_id") else None,
        }

        # Add event-specific fields
        if isinstance(event, InventoryAdded):
            base.update({
                "item_id": str(event.item_id),
                "location_id": str(event.location_id),
                "quantity_added": str(event.quantity_added),
                "quantity_after": str(event.quantity_after),
                "unit": event.unit,
                "reason": event.reason,
            })
        elif isinstance(event, InventoryRemoved):
            base.update({
                "item_id": str(event.item_id),
                "location_id": str(event.location_id),
                "quantity_removed": str(event.quantity_removed),
                "quantity_after": str(event.quantity_after),
                "unit": event.unit,
                "reason": event.reason,
            })
        elif isinstance(event, InventoryAdjusted):
            base.update({
                "item_id": str(event.item_id),
                "location_id": str(event.location_id),
                "quantity_before": str(event.quantity_before),
                "quantity_after": str(event.quantity_after),
                "unit": event.unit,
                "reason": event.reason,
            })
        elif isinstance(event, InventoryMoved):
            base.update({
                "item_id": str(event.item_id),
                "source_location_id": str(event.source_location_id),
                "target_location_id": str(event.target_location_id),
                "quantity_moved": str(event.quantity_moved),
                "unit": event.unit,
                "reason": event.reason,
            })

        return base
