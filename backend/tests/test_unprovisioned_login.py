"""Password login is only for human-provisioned accounts.

Personal/kiosk mode auto-creates an admin user (``get_or_create_personal_user``)
so open-mode requests have an identity. That account's password comes from
``AUTH_PERSONAL_PASSWORD``, whose built-in default is public. It must never be
usable for password login; only accounts a person set up (first-run admin,
user management, SSO) may sign in.
"""

from __future__ import annotations

import httpx

from app.db import SessionLocal
from app.services.auth import get_or_create_personal_user

KNOWN_DEFAULT = "TestPass123!"


def _login(client: httpx.Client, email: str, password: str) -> httpx.Response:
    return client.post("/api/auth/login", json={"email": email, "password": password})


def _make_personal_user(email: str = "admin@pantryos.local") -> None:
    db = SessionLocal()
    try:
        get_or_create_personal_user(db, email=email, password=KNOWN_DEFAULT)
    finally:
        db.close()


def test_auto_created_personal_user_cannot_log_in(client: httpx.Client) -> None:
    _make_personal_user()

    resp = _login(client, "admin@pantryos.local", KNOWN_DEFAULT)

    assert resp.status_code == 401
    assert resp.json()["detail"] == "Invalid credentials"


def test_first_run_admin_claims_personal_user_and_can_log_in(client: httpx.Client) -> None:
    _make_personal_user()

    boot = client.post(
        "/api/auth/bootstrap-admin",
        json={"email": "owner@example.com", "password": "CorrectHorse9!"},
    )
    assert boot.status_code == 200

    assert _login(client, "owner@example.com", "CorrectHorse9!").status_code == 200
    # The claimed account no longer answers to the old address/default password.
    assert _login(client, "admin@pantryos.local", KNOWN_DEFAULT).status_code == 401


def test_stray_personal_user_stays_locked_when_admin_is_created_separately(client: httpx.Client) -> None:
    # Two users exist before first-run setup, so bootstrap creates a new admin
    # instead of claiming the auto-user. The auto-user keeps the known default
    # password but must still be unable to log in with it.
    _make_personal_user()
    db = SessionLocal()
    try:
        from app.models import User

        db.add(User(email="member@example.com", password_hash="x", role="member", is_active=True))
        db.commit()
    finally:
        db.close()

    boot = client.post(
        "/api/auth/bootstrap-admin",
        json={"email": "owner@example.com", "password": "CorrectHorse9!"},
    )
    assert boot.status_code == 200

    assert _login(client, "owner@example.com", "CorrectHorse9!").status_code == 200
    assert _login(client, "admin@pantryos.local", KNOWN_DEFAULT).status_code == 401
