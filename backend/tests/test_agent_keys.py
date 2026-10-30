"""Agent keys: mint once, authenticate any protected route, list, revoke."""

from __future__ import annotations

import os
import uuid

import httpx

API_BASE = os.getenv("API_BASE_URL", "http://localhost:8000")


def _auth_headers() -> dict[str, str]:
    email = os.getenv("TEST_ADMIN_EMAIL", "admin@pantryos.local")
    password = os.getenv("TEST_ADMIN_PASSWORD", "TestPass123!")
    httpx.post(f"{API_BASE}/api/auth/bootstrap-admin", json={"email": email, "password": password}, timeout=10)
    login = httpx.post(f"{API_BASE}/api/auth/login", json={"email": email, "password": password}, timeout=10)
    assert login.status_code == 200
    headers = {"Authorization": f"Bearer {login.json()['token']}"}
    return headers


def test_agent_key_lifecycle_and_auth() -> None:
    headers = _auth_headers()

    # Mint a key — the plaintext secret is returned exactly once.
    created = httpx.post(
        f"{API_BASE}/api/agent-keys", json={"name": "Hermes"}, headers=headers, timeout=10
    )
    assert created.status_code == 200
    body = created.json()
    secret = body["secret"]
    key_id = body["key"]["id"]
    assert secret.startswith("pk_")
    assert body["key"]["token_prefix"] == secret[:7]
    assert body["key"]["revoked"] is False

    # The key authenticates a protected route on its own (no bearer token).
    agent_headers = {"X-Agent-Key": secret}

    me = httpx.get(f"{API_BASE}/api/auth/me", headers=agent_headers, timeout=10)
    assert me.status_code == 200

    # And it can drive real work — e.g. add a shopping-list need.
    need = httpx.post(
        f"{API_BASE}/api/shopping-list", json={"name": "Agent bought this"}, headers=agent_headers, timeout=10
    )
    assert need.status_code == 201

    # It appears in the (masked) list without the secret.
    listed = httpx.get(f"{API_BASE}/api/agent-keys", headers=headers, timeout=10).json()["keys"]
    assert any(k["id"] == key_id for k in listed)
    assert all("secret" not in k for k in listed)

    # Revoke it -> the key no longer authenticates.
    revoked = httpx.delete(f"{API_BASE}/api/agent-keys/{key_id}", headers=headers, timeout=10)
    assert revoked.status_code == 200 and revoked.json()["revoked"] is True

    denied = httpx.get(f"{API_BASE}/api/auth/me", headers=agent_headers, timeout=10)
    assert denied.status_code == 401


def test_invalid_agent_key_rejected() -> None:
    headers = _auth_headers()
    agent_headers = {"X-Agent-Key": f"pk_{uuid.uuid4().hex}"}
    resp = httpx.get(f"{API_BASE}/api/auth/me", headers=agent_headers, timeout=10)
    assert resp.status_code == 401


def test_read_only_scope_blocks_writes() -> None:
    headers = _auth_headers()
    created = httpx.post(
        f"{API_BASE}/api/agent-keys", json={"name": "readonly", "scopes": "read"}, headers=headers, timeout=10
    )
    assert created.status_code == 200
    assert created.json()["key"]["scopes"] == "read"
    secret = created.json()["secret"]

    agent_headers = {"X-Agent-Key": secret}

    # Reads are allowed...
    assert httpx.get(f"{API_BASE}/api/auth/me", headers=agent_headers, timeout=10).status_code == 200
    # ...but writes are refused with 403.
    blocked = httpx.post(
        f"{API_BASE}/api/shopping-list", json={"name": "nope"}, headers=agent_headers, timeout=10
    )
    assert blocked.status_code == 403


def test_unknown_scope_rejected() -> None:
    headers = _auth_headers()
    resp = httpx.post(
        f"{API_BASE}/api/agent-keys", json={"name": "bad", "scopes": "read,admin"}, headers=headers, timeout=10
    )
    assert resp.status_code == 400
