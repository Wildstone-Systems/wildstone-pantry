from __future__ import annotations

import os

import httpx


API_BASE = os.getenv("API_BASE_URL", "http://localhost:8000")


def _is_auth_enabled() -> bool:
    """
    Detect backend auth mode.

    - AUTH_ENABLED=true: /api/auth/me requires a bearer token => 401 without it.
    - AUTH_ENABLED=false: /api/auth/me returns the personal-mode user => 200 without it.
    """

    res = httpx.get(f"{API_BASE}/api/auth/me", timeout=10)
    if res.status_code == 401:
        return True
    assert res.status_code == 200
    return False


def test_phase15_auth_optional_personal_mode_toggle() -> None:
    auth_enabled = _is_auth_enabled()

    # Probes a *gated* surface. /api/items is deliberately not one: catalog is
    # kiosk-open (ADR 0004) so a tablet can add stock with nobody logged in.
    protected = httpx.get(f"{API_BASE}/api/auth/users", timeout=10)
    if auth_enabled:
        assert protected.status_code == 401
    else:
        assert protected.status_code == 200


def test_phase15_catalog_is_kiosk_open_in_both_modes() -> None:
    """Catalog reads never require a token — that is what makes the kiosk usable."""

    items = httpx.get(f"{API_BASE}/api/items", timeout=10)
    assert items.status_code == 200


def test_phase15_login_still_works_when_auth_enabled() -> None:
    """
    Even if personal mode is enabled, login should keep working (so switching auth
    back on later doesn't require code changes).
    """

    email = os.getenv("TEST_ADMIN_EMAIL", "admin@pantryos.local")
    password = os.getenv("TEST_ADMIN_PASSWORD", "TestPass123!")

    httpx.post(f"{API_BASE}/api/auth/bootstrap-admin", json={"email": email, "password": password}, timeout=10)
    login = httpx.post(f"{API_BASE}/api/auth/login", json={"email": email, "password": password}, timeout=10)
    assert login.status_code == 200

    token = login.json()["token"]
    headers = {"Authorization": f"Bearer {token}"}

    me = httpx.get(f"{API_BASE}/api/auth/me", headers=headers, timeout=10)
    assert me.status_code == 200
