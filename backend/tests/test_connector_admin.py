"""Admin Integrations API — in-app connector config, encrypted at rest (ADR 0009)."""

from __future__ import annotations

from datetime import UTC

import httpx


def _admin_headers(client: httpx.Client) -> dict[str, str]:
    client.post("/api/auth/bootstrap-admin", json={"email": "admin@pantryos.local", "password": "TestPass123!"})
    login = client.post("/api/auth/login", json={"email": "admin@pantryos.local", "password": "TestPass123!"})
    return {"Authorization": f"Bearer {login.json()['token']}"}


def test_requires_admin_login(client: httpx.Client) -> None:
    assert client.get("/api/admin/integrations/instacart").status_code == 401


def test_get_config_defaults_to_env_only(client: httpx.Client) -> None:
    h = _admin_headers(client)
    body = client.get("/api/admin/integrations/instacart", headers=h).json()
    assert body["key"] == "instacart"
    assert body["from_env_only"] is True
    assert body["has_override"] is False
    assert body["secret_set"]["api_key"] is False
    assert body["secrets_available"] is True  # conftest sets CONFIG_SECRET_KEY


def test_set_config_stores_values_and_hides_secrets(client: httpx.Client) -> None:
    h = _admin_headers(client)
    resp = client.put(
        "/api/admin/integrations/instacart",
        json={"updates": {"base_url": "https://connect.example.com", "api_key": "app-key"}},
        headers=h,
    )
    assert resp.status_code == 200
    view = resp.json()
    assert view["values"]["base_url"] == "https://connect.example.com"
    assert view["secret_set"]["api_key"] is True
    assert view["has_override"] is True
    assert view["from_env_only"] is False
    # The secret is never echoed back anywhere in the view.
    assert "app-key" not in resp.text


def test_partial_update_keeps_existing_secret(client: httpx.Client) -> None:
    h = _admin_headers(client)
    client.put(
        "/api/admin/integrations/instacart",
        json={"updates": {"base_url": "https://a.example.com", "api_key": "secret1"}},
        headers=h,
    )
    # Update only the base URL; omit api_key → it must be retained.
    client.put("/api/admin/integrations/instacart", json={"updates": {"base_url": "https://b.example.com"}}, headers=h)
    view = client.get("/api/admin/integrations/instacart", headers=h).json()
    assert view["values"]["base_url"] == "https://b.example.com"
    assert view["secret_set"]["api_key"] is True


def test_clear_reverts_to_env(client: httpx.Client) -> None:
    h = _admin_headers(client)
    client.put("/api/admin/integrations/instacart", json={"updates": {"api_key": "p"}}, headers=h)
    client.delete("/api/admin/integrations/instacart", headers=h)
    view = client.get("/api/admin/integrations/instacart", headers=h).json()
    assert view["has_override"] is False
    assert view["from_env_only"] is True


def test_unknown_connector_is_404(client: httpx.Client) -> None:
    h = _admin_headers(client)
    assert client.get("/api/admin/integrations/nope", headers=h).status_code == 404
    assert client.put("/api/admin/integrations/nope", json={"updates": {}}, headers=h).status_code == 404


def test_unprovisioned_admin_is_denied(client: httpx.Client) -> None:
    """The kiosk auto-user is role=admin but unprovisioned; it must not reach the
    protected admin surface even with a valid token (ADR 0004 / GH #20 review)."""
    from datetime import datetime, timedelta

    from app.db import SessionLocal
    from app.models import AuthToken, User
    from app.services.auth import _password_record  # type: ignore[attr-defined]

    db = SessionLocal()
    try:
        user = User(
            email="kiosk-auto@pantryos.local",
            password_hash=_password_record("TestPass123!"),
            role="admin",
            is_active=True,
            is_provisioned=False,
        )
        db.add(user)
        db.commit()
        db.refresh(user)
        now = datetime.now(UTC)
        tok = AuthToken(
            user_id=user.id,
            token="unprovisioned-admin-token",
            created_at=now,
            expires_at=now + timedelta(hours=1),
        )
        db.add(tok)
        db.commit()
    finally:
        db.close()

    resp = client.get(
        "/api/admin/integrations/instacart", headers={"Authorization": "Bearer unprovisioned-admin-token"}
    )
    assert resp.status_code == 403
    assert "provision" in resp.json()["detail"].lower()


def test_ciphertext_at_rest_is_not_plaintext(client: httpx.Client) -> None:
    from app.db import SessionLocal
    from app.models import ConnectorConfig

    h = _admin_headers(client)
    client.put(
        "/api/admin/integrations/instacart",
        json={"updates": {"api_key": "topsecret"}},
        headers=h,
    )
    db = SessionLocal()
    try:
        row = db.get(ConnectorConfig, "instacart")
        assert row is not None
        assert "topsecret" not in row.ciphertext  # encrypted, not stored in the clear
    finally:
        db.close()
