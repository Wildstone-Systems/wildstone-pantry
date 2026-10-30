from __future__ import annotations

import os

import httpx


API_BASE = os.getenv("API_BASE_URL", "http://localhost:8000")


def test_phase5_auth_flow() -> None:
    email = os.getenv("TEST_ADMIN_EMAIL", "admin@pantryos.local")
    password = os.getenv("TEST_ADMIN_PASSWORD", "TestPass123!")

    # A gated surface, not /api/items — catalog is kiosk-open by design (ADR 0004).
    unauthorized = httpx.get(f"{API_BASE}/api/auth/users", timeout=10)
    assert unauthorized.status_code == 401

    bootstrap = httpx.post(
        f"{API_BASE}/api/auth/bootstrap-admin",
        json={"email": email, "password": password},
        timeout=10,
    )
    assert bootstrap.status_code in {200, 409}

    login = httpx.post(
        f"{API_BASE}/api/auth/login",
        json={"email": email, "password": password},
        timeout=10,
    )
    assert login.status_code == 200
    token = login.json()["token"]
    headers = {"Authorization": f"Bearer {token}"}

    me = httpx.get(f"{API_BASE}/api/auth/me", headers=headers, timeout=10)
    assert me.status_code == 200
    assert me.json()["role"] in {"admin", "user"}

    users = httpx.get(f"{API_BASE}/api/auth/users", headers=headers, timeout=10)
    assert users.status_code == 200

    # ADR 0018: the legacy shared-password license endpoints are gone.
    assert httpx.get(f"{API_BASE}/api/license/status", timeout=10).status_code == 404
