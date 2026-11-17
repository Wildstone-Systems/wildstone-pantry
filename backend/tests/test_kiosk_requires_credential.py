"""Kiosk surfaces need a credential once auth is on (ADR 0004, updated).

With ``AUTH_ENABLED`` off (trusted network), a request with no credential is
the personal identity, as before. With auth on, a wall tablet pairs once and
presents its device key; a bare request gets 401. A presented-but-invalid
credential is always an error, never a silent fallback.
"""

from __future__ import annotations

import httpx
import pytest

from app.core.config import settings

# One representative route per kiosk-open module.
KIOSK_ROUTES = [
    "/api/items",
    "/api/locations",
    "/api/inventory",
    "/api/shopping-list",
    "/api/recipes",
]


def _admin_headers(client: httpx.Client) -> dict[str, str]:
    email, password = "kiosk-admin@example.com", "KioskPass9!"
    client.post("/api/auth/bootstrap-admin", json={"email": email, "password": password})
    login = client.post("/api/auth/login", json={"email": email, "password": password})
    return {"Authorization": f"Bearer {login.json()['token']}"}


def _device_headers(client: httpx.Client) -> dict[str, str]:
    code = client.post("/api/devices/pairing-codes", headers=_admin_headers(client)).json()["code"]
    paired = client.post("/api/devices/pair", json={"code": code, "device_name": "Kitchen tablet"})
    assert paired.status_code == 200, paired.text
    return {"Authorization": f"Bearer {paired.json()['device_key']}"}


@pytest.mark.parametrize("path", KIOSK_ROUTES)
def test_no_credential_is_rejected_while_auth_is_enabled(client: httpx.Client, monkeypatch, path: str) -> None:
    monkeypatch.setattr(settings, "auth_enabled", True)
    resp = client.get(path)
    assert resp.status_code == 401
    assert resp.headers.get("X-PantryOS-Auth") == "required"


def test_paired_kiosk_tablet_can_still_add_items(client: httpx.Client, monkeypatch) -> None:
    monkeypatch.setattr(settings, "auth_enabled", True)
    tablet = _device_headers(client)

    assert client.post("/api/locations", json={"name": "Pantry"}, headers=tablet).status_code == 201
    assert client.post("/api/items", json={"name": "Olive Oil"}, headers=tablet).status_code == 201
    listed = client.get("/api/items", headers=tablet)
    assert listed.status_code == 200
    assert any(row["name"] == "Olive Oil" for row in listed.json()["items"])


def test_agent_key_reaches_kiosk_routes_while_auth_is_enabled(client: httpx.Client, monkeypatch) -> None:
    monkeypatch.setattr(settings, "auth_enabled", True)
    secret = client.post("/api/agent-keys", json={"name": "n8n"}, headers=_admin_headers(client)).json()["secret"]
    assert client.get("/api/shopping-list", headers={"X-Agent-Key": secret}).status_code == 200


def test_open_mode_still_works_without_a_credential(client: httpx.Client, monkeypatch) -> None:
    monkeypatch.setattr(settings, "auth_enabled", False)
    assert client.post("/api/items", json={"name": "Rice"}).status_code == 201


def test_bad_token_is_rejected_rather_than_downgraded(client: httpx.Client, monkeypatch) -> None:
    monkeypatch.setattr(settings, "auth_enabled", True)
    resp = client.post("/api/items", json={"name": "Rice"}, headers={"Authorization": "Bearer not-a-real-token"})
    assert resp.status_code == 401
