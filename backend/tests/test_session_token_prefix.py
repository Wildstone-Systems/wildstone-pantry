"""Session tokens must never be mistaken for device keys.

``require_user`` routes any ``Bearer`` token starting with ``dk_`` (case-
insensitively) to device-key authentication. A random session token from
``secrets.token_urlsafe`` used to hit that prefix about once in 65,536 logins,
which then failed every request with "Invalid or revoked device key".
"""

from __future__ import annotations

import httpx

from app.services import auth


def test_session_tokens_never_use_the_device_key_prefix(client: httpx.Client, monkeypatch) -> None:
    draws = iter(["dK_" + "a" * 61, "DK_" + "b" * 61, "session" + "c" * 57])
    monkeypatch.setattr(auth.secrets, "token_urlsafe", lambda nbytes=None: next(draws))

    email, password = "prefix-admin@example.com", "TestPass123!"
    client.post("/api/auth/bootstrap-admin", json={"email": email, "password": password})
    login = client.post("/api/auth/login", json={"email": email, "password": password})
    assert login.status_code == 200, login.text
    token = login.json()["token"]

    assert not token.lower().startswith("dk_")
    me = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert me.status_code == 200, me.text
