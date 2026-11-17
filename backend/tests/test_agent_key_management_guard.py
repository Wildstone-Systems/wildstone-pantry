"""Who may manage agent keys.

Only a logged-in person: never an agent key (a leaked key must not be able to
mint successors that outlive its revocation), never a device key, never the
open personal identity. Members see and revoke only their own keys.
"""

from __future__ import annotations

import httpx
import pytest

from app.core.config import settings
from app.db import SessionLocal
from app.services.auth import create_user


def _login(client: httpx.Client, email: str, password: str) -> dict[str, str]:
    token = client.post("/api/auth/login", json={"email": email, "password": password}).json()["token"]
    return {"Authorization": f"Bearer {token}"}


def _admin(client: httpx.Client) -> dict[str, str]:
    email, password = "keys-admin@example.com", "KeysAdmin9!"
    client.post("/api/auth/bootstrap-admin", json={"email": email, "password": password})
    return _login(client, email, password)


def _member(client: httpx.Client) -> dict[str, str]:
    with SessionLocal() as db:
        create_user(db, email="keys-member@example.com", password="KeysMember9!", role="member")
    return _login(client, "keys-member@example.com", "KeysMember9!")


@pytest.mark.parametrize("auth_on", [True, False])
def test_no_credential_cannot_manage_keys(client: httpx.Client, monkeypatch, auth_on: bool) -> None:
    monkeypatch.setattr(settings, "auth_enabled", auth_on)
    assert client.get("/api/agent-keys").status_code == 401
    assert client.post("/api/agent-keys", json={"name": "x"}).status_code == 401


def test_agent_key_cannot_mint_list_or_revoke_keys(client: httpx.Client) -> None:
    created = client.post("/api/agent-keys", json={"name": "leaky", "scopes": "read,write"}, headers=_admin(client))
    agent = {"X-Agent-Key": created.json()["secret"]}

    assert client.post("/api/agent-keys", json={"name": "successor"}, headers=agent).status_code == 403
    assert client.get("/api/agent-keys", headers=agent).status_code == 403
    assert client.delete(f"/api/agent-keys/{created.json()['key']['id']}", headers=agent).status_code == 403


def test_device_key_cannot_mint_keys(client: httpx.Client) -> None:
    code = client.post("/api/devices/pairing-codes", headers=_admin(client)).json()["code"]
    device_key = client.post("/api/devices/pair", json={"code": code, "device_name": "Tablet"}).json()["device_key"]
    resp = client.post("/api/agent-keys", json={"name": "x"}, headers={"Authorization": f"Bearer {device_key}"})
    assert resp.status_code == 403


def test_members_only_see_and_revoke_their_own_keys(client: httpx.Client) -> None:
    admin, member = _admin(client), _member(client)
    admin_key = client.post("/api/agent-keys", json={"name": "admin-key"}, headers=admin).json()["key"]["id"]
    member_key = client.post("/api/agent-keys", json={"name": "member-key"}, headers=member).json()["key"]["id"]

    member_view = {k["id"] for k in client.get("/api/agent-keys", headers=member).json()["keys"]}
    assert member_view == {member_key}
    admin_view = {k["id"] for k in client.get("/api/agent-keys", headers=admin).json()["keys"]}
    assert {admin_key, member_key} <= admin_view

    assert client.delete(f"/api/agent-keys/{admin_key}", headers=member).status_code == 404
    assert client.delete(f"/api/agent-keys/{member_key}", headers=member).status_code == 200
    # Admin can revoke anyone's key.
    other = client.post("/api/agent-keys", json={"name": "member-2"}, headers=member).json()["key"]["id"]
    assert client.delete(f"/api/agent-keys/{other}", headers=admin).status_code == 200
