"""IN purchase connectors — IMAP email intake (step 5b).

The parse pipeline is exercised against fixture emails (no live mailbox); the
connector itself is verified dormant-by-default via the API.
"""

from __future__ import annotations

from decimal import Decimal
from email.message import EmailMessage
from uuid import UUID

import httpx
import pytest

from app.db import SessionLocal
from modules.shopping.connectors import NormalizedLine, NormalizedOrder, email_to_order
from modules.shopping.connectors.imap import ImapPurchaseConnector, message_to_order
from modules.shopping.services.purchase_connector_import import import_orders

# --- A realistic Kroger order-confirmation email (HTML), used as a fixture. ----

KROGER_HTML = """
<html><body>
  <h1>Your Kroger receipt</h1>
  <p>Store #402</p>
  <p>Date: 07/20/2026</p>
  <p>Whole Milk $3.49</p>
  <p>Large Eggs $2.99</p>
  <p>Subtotal: $6.48</p>
  <p>Tax: $0.00</p>
  <p>Total: $6.48</p>
</body></html>
"""


def _build_raw_email() -> bytes:
    msg = EmailMessage()
    msg["From"] = "Kroger <receipts@kroger.com>"
    msg["Subject"] = "Your Kroger order confirmation"
    msg["To"] = "family@example.com"
    msg.set_content("Plain text fallback")
    msg.add_alternative(KROGER_HTML, subtype="html")
    return msg.as_bytes()


# --- Pure parse pipeline (fixture-testable core) -------------------------------


def test_email_to_order_parses_kroger_html() -> None:
    order = email_to_order(
        sender="Kroger <receipts@kroger.com>",
        subject="Your Kroger order confirmation",
        html=KROGER_HTML,
        text=None,
    )
    assert order is not None
    assert order.vendor == "kroger"
    assert order.purchased_on is not None and order.purchased_on.isoformat() == "2026-07-20"
    names = {line.name for line in order.lines}
    assert "Whole Milk" in names
    assert "Large Eggs" in names
    milk = next(line for line in order.lines if line.name == "Whole Milk")
    assert milk.unit_price == Decimal("3.49")
    assert milk.quantity == Decimal("1")


def test_email_to_order_returns_none_when_no_items() -> None:
    order = email_to_order(
        sender="someone@example.com",
        subject="hello",
        html="<html><body><p>Just a note, nothing bought.</p></body></html>",
        text=None,
    )
    assert order is None


def test_message_to_order_parses_multipart_rfc822() -> None:
    order = message_to_order(_build_raw_email())
    assert order is not None
    assert order.vendor == "kroger"
    assert len(order.lines) == 2


# --- Connector is dormant by default -------------------------------------------


def test_imap_connector_configured_reflects_config() -> None:
    conn = ImapPurchaseConnector()
    assert conn.configured({}) is False
    assert conn.configured({"host": "imap.example.com", "username": "u", "password": "p"}) is True


# --- API contract --------------------------------------------------------------


def _headers(client: httpx.Client) -> dict[str, str]:
    client.post("/api/auth/bootstrap-admin", json={"email": "admin@pantryos.local", "password": "TestPass123!"})
    login = client.post("/api/auth/login", json={"email": "admin@pantryos.local", "password": "TestPass123!"})
    return {"Authorization": f"Bearer {login.json()['token']}"}


def test_connectors_list_includes_dormant_imap(client: httpx.Client) -> None:
    h = _headers(client)
    connectors = {
        c["key"]: c for c in client.get("/api/purchases/connectors", headers=h).json()["connectors"]
    }
    assert "imap" in connectors
    assert connectors["imap"]["configured"] is False


def test_dormant_imap_preview_returns_409(client: httpx.Client) -> None:
    h = _headers(client)
    resp = client.post("/api/purchases/connectors/imap/preview", headers=h)
    assert resp.status_code == 409
    assert "not configured" in resp.json()["detail"].lower()


def test_unknown_connector_preview_returns_404(client: httpx.Client) -> None:
    h = _headers(client)
    resp = client.post("/api/purchases/connectors/nope/preview", headers=h)
    assert resp.status_code == 404


def test_dormant_imap_import_returns_409(client: httpx.Client) -> None:
    h = _headers(client)
    loc = client.post("/api/locations", json={"name": "Pantry"}, headers=h).json()["id"]
    resp = client.post("/api/purchases/connectors/imap/import", json={"location_id": loc}, headers=h)
    assert resp.status_code == 409
    assert "not configured" in resp.json()["detail"].lower()


def test_import_unknown_connector_returns_404(client: httpx.Client) -> None:
    h = _headers(client)
    loc = client.post("/api/locations", json={"name": "Pantry"}, headers=h).json()["id"]
    resp = client.post("/api/purchases/connectors/nope/import", json={"location_id": loc}, headers=h)
    assert resp.status_code == 404


# --- import_orders: the network-free commit core, fixture-tested ----------------


def _fixture_order() -> NormalizedOrder:
    return NormalizedOrder(
        vendor="kroger",
        purchased_on=None,
        external_id="<msg-abc-123@kroger.com>",
        lines=[
            NormalizedLine(name="Whole Milk", quantity=Decimal("2"), unit="gal"),
            NormalizedLine(name="Large Eggs", quantity=Decimal("1"), unit="each"),
        ],
    )


def test_import_orders_lands_intake_events_and_is_idempotent(client: httpx.Client) -> None:
    h = _headers(client)
    loc = client.post("/api/locations", json={"name": "Pantry"}, headers=h).json()["id"]

    order = _fixture_order()
    db = SessionLocal()
    try:
        first = import_orders(db, [order], location_id=UUID(loc))
        assert first.orders == 1
        assert first.imported_orders == 1
        assert first.imported_lines == 2
        assert first.skipped_orders == 0

        # Re-importing the same order (same external_id) is skipped, not doubled.
        second = import_orders(db, [order], location_id=UUID(loc))
        assert second.skipped_orders == 1
        assert second.imported_lines == 0
    finally:
        db.close()

    # The intake shows up in inventory (event-sourced on-hand).
    rows = client.get("/api/inventory", params={"location_id": loc}, headers=h).json()["items"]
    on_hand = {r["item_name"]: Decimal(str(r["quantity"])) for r in rows}
    assert on_hand.get("Whole Milk") == Decimal("2")
    assert on_hand.get("Large Eggs") == Decimal("1")


def test_import_orders_unknown_location_raises() -> None:
    db = SessionLocal()
    try:
        with pytest.raises(LookupError):
            import_orders(db, [_fixture_order()], location_id=UUID("00000000-0000-0000-0000-000000000000"))
    finally:
        db.close()
