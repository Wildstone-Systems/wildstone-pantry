"""First-run setup wizard (ADR 0018 §7): open while incomplete, sealed after."""

from __future__ import annotations

import httpx

from app.core.config import settings


def test_fresh_instance_reports_setup_needed(client: httpx.Client) -> None:
    status = client.get("/api/setup/status").json()
    assert status["completed"] is False
    assert status["needs_admin"] is True
    assert status["household_name"] is None
    assert status["accounts_mode"] is False


def test_wizard_flow_open_mode(client: httpx.Client) -> None:
    assert client.post("/api/setup/household", json={"name": "The Halls"}).status_code == 200

    # Open mode demands the explicit anyone-on-WiFi acknowledgment (Q43-2).
    refused = client.post("/api/setup/mode", json={"mode": "open", "lan_ack": False})
    assert refused.status_code == 400
    assert client.post("/api/setup/mode", json={"mode": "open", "lan_ack": True}).status_code == 200

    seeded = client.post("/api/setup/seed-defaults", json={"preset": "typical"}).json()
    assert seeded["created"]["locations"] == 4
    assert seeded["created"]["categories"] == 4
    # Idempotent: re-seeding creates nothing new.
    again = client.post("/api/setup/seed-defaults", json={"preset": "typical"}).json()
    assert again["created"] == {"locations": 0, "categories": 0}

    locations = client.get("/api/locations").json()["locations"]
    assert {loc["name"] for loc in locations} >= {"Pantry", "Fridge", "Freezer", "Garage"}

    assert client.post("/api/setup/complete").json()["completed"] is True
    status = client.get("/api/setup/status").json()
    assert status["completed"] is True
    assert status["household_name"] == "The Halls"

    # Sealed: no open reconfiguration surface on a lived-in instance.
    assert client.post("/api/setup/household", json={"name": "x"}).status_code == 403
    assert client.post("/api/setup/mode", json={"mode": "open", "lan_ack": True}).status_code == 403
    assert client.post("/api/setup/seed-defaults", json={"preset": "minimal"}).status_code == 403
    # Complete stays idempotent (the wizard can always be dismissed).
    assert client.post("/api/setup/complete").status_code == 200


def test_accounts_mode_turns_auth_on_at_runtime(client: httpx.Client, monkeypatch) -> None:
    # Kiosk env (AUTH_ENABLED=false): protected surfaces fall back to the
    # personal admin user...
    monkeypatch.setattr(settings, "auth_enabled", False)
    assert client.get("/api/auth/users").status_code == 200

    # ...until the wizard's "household accounts" choice persists the override.
    assert client.post("/api/setup/mode", json={"mode": "accounts"}).status_code == 200
    assert client.get("/api/auth/users").status_code == 401
    assert client.get("/api/setup/status").json()["accounts_mode"] is True

    # Kiosk-open endpoints stay open either way (ADR 0004).
    assert client.get("/api/items").status_code == 200


def test_existing_install_with_items_never_sees_the_wizard(client: httpx.Client) -> None:
    created = client.post("/api/items", json={"name": "Pre-wizard beans"})
    assert created.status_code in {200, 201}
    assert client.get("/api/setup/status").json()["completed"] is True
