"""First-run admin setup works in kiosk mode (ADR 0004).

In kiosk mode (AUTH_ENABLED=false) the everyday endpoints auto-create an
unprovisioned personal user. Bootstrap must still be able to set up the real
admin (claiming that auto-user) instead of failing with 409, and must refuse a
second time once a real admin is provisioned.
"""

from __future__ import annotations

import httpx

from app.core.config import settings


def test_bootstrap_succeeds_after_kiosk_autocreated_a_personal_user(client: httpx.Client, monkeypatch) -> None:
    monkeypatch.setattr(settings, "auth_enabled", False)

    # A kiosk everyday call auto-creates the (unprovisioned) personal user.
    assert client.get("/api/auth/me").status_code == 200
    assert client.get("/api/auth/setup-status").json()["needs_setup"] is True

    # First-run setup still works (claims the auto-user), no 409.
    resp = client.post("/api/auth/bootstrap-admin", json={"email": "parent@home.local", "password": "KitchenBoss1"})
    assert resp.status_code == 200
    assert resp.json()["email"] == "parent@home.local"
    assert client.get("/api/auth/setup-status").json()["needs_setup"] is False

    # The provisioned admin can log in with the chosen credentials.
    login = client.post("/api/auth/login", json={"email": "parent@home.local", "password": "KitchenBoss1"})
    assert login.status_code == 200
    assert login.json()["role"] == "admin"

    # A second bootstrap is refused now that a real admin exists.
    second = client.post("/api/auth/bootstrap-admin", json={"email": "someone@else.local", "password": "Another123"})
    assert second.status_code == 409


def test_setup_status_true_on_empty_then_false_after_bootstrap(client: httpx.Client) -> None:
    assert client.get("/api/auth/setup-status").json()["needs_setup"] is True
    client.post("/api/auth/bootstrap-admin", json={"email": "a@b.local", "password": "Password12"})
    assert client.get("/api/auth/setup-status").json()["needs_setup"] is False
