"""OIDC config plumbing (ADR 0020 Chunk A): _SPECS entry, service skeleton,
and the local-login-visibility toggle."""

from __future__ import annotations

import httpx

from app.db import SessionLocal
from app.services import app_settings, oidc
from modules.shopping.services import connector_config


def _admin_headers(client: httpx.Client) -> dict[str, str]:
    client.post("/api/auth/bootstrap-admin", json={"email": "admin@pantryos.local", "password": "TestPass123!"})
    login = client.post("/api/auth/login", json={"email": "admin@pantryos.local", "password": "TestPass123!"})
    return {"Authorization": f"Bearer {login.json()['token']}"}


def test_defaults_applied_when_unset() -> None:
    db = SessionLocal()
    try:
        cfg = oidc.effective_config(db)
        assert cfg["scopes"] == "openid profile email groups"
        assert cfg["groups_claim"] == "groups"
        assert cfg["users_group"] == "pantryos_users"
        assert cfg["admin_group"] == "pantryos_admin"
        assert cfg["button_label"] == "Sign in with SSO"
        assert cfg.get("issuer", "") == ""
        assert cfg.get("client_id", "") == ""
        assert cfg.get("client_secret", "") == ""
    finally:
        db.close()


def test_enabled_false_when_unset_true_when_configured() -> None:
    db = SessionLocal()
    try:
        assert oidc.enabled(db) is False
        connector_config.set_config(
            db,
            "oidc",
            {
                "issuer": "https://auth.example.lan/application/o/pantryos/",
                "client_id": "pantryos",
                "client_secret": "s3cret",
            },
        )
        assert oidc.enabled(db) is True
    finally:
        db.close()


def test_config_override_replaces_defaults() -> None:
    db = SessionLocal()
    try:
        connector_config.set_config(
            db,
            "oidc",
            {
                "issuer": "https://auth.example.lan/application/o/pantryos/",
                "client_id": "pantryos",
                "client_secret": "s3cret",
                "scopes": "openid email",
                "button_label": "Log in with Authelia",
            },
        )
        cfg = oidc.effective_config(db)
        assert cfg["scopes"] == "openid email"
        assert cfg["button_label"] == "Log in with Authelia"
        # Untouched fields still fall back to defaults.
        assert cfg["groups_claim"] == "groups"
    finally:
        db.close()


def test_redirect_uri_uses_request_base_url() -> None:
    request = httpx.Request("GET", "http://pantryos.local/api/auth/oidc/login")

    class FakeRequest:
        base_url = "http://pantryos.local/"

    assert oidc.redirect_uri(FakeRequest()) == "http://pantryos.local/api/auth/oidc/callback"
    del request  # unused; kept to document base_url shape expected from a real Request


def test_admin_integrations_oidc_stores_and_redacts_secret(client: httpx.Client) -> None:
    h = _admin_headers(client)

    body = client.get("/api/admin/integrations/oidc", headers=h).json()
    assert body["key"] == "oidc"
    assert body["from_env_only"] is True
    assert body["has_override"] is False
    assert body["secret_set"]["client_secret"] is False

    resp = client.put(
        "/api/admin/integrations/oidc",
        json={
            "updates": {
                "issuer": "https://auth.example.lan/application/o/pantryos/",
                "client_id": "pantryos",
                "client_secret": "s3cret",
                "button_label": "Sign in with SSO",
            }
        },
        headers=h,
    )
    assert resp.status_code == 200
    view = resp.json()
    assert view["values"]["issuer"] == "https://auth.example.lan/application/o/pantryos/"
    assert view["values"]["client_id"] == "pantryos"
    assert view["secret_set"]["client_secret"] is True
    assert view["has_override"] is True
    assert view["from_env_only"] is False
    # The secret is never echoed back anywhere in the view.
    assert "s3cret" not in resp.text

    view2 = client.get("/api/admin/integrations/oidc", headers=h).json()
    assert view2["secret_set"]["client_secret"] is True
    assert "client_secret" not in view2["values"]


def test_local_login_visibility_requires_admin(client: httpx.Client) -> None:
    resp = client.put("/api/auth/local-login-visibility", json={"hidden": True})
    assert resp.status_code == 401


def test_local_login_visibility_round_trips(client: httpx.Client) -> None:
    h = _admin_headers(client)

    resp = client.put("/api/auth/local-login-visibility", json={"hidden": True}, headers=h)
    assert resp.status_code == 200
    assert resp.json() == {"hidden": True}

    db = SessionLocal()
    try:
        assert app_settings.is_true(db, app_settings.KEY_LOCAL_LOGIN_HIDDEN) is True
    finally:
        db.close()

    resp = client.put("/api/auth/local-login-visibility", json={"hidden": False}, headers=h)
    assert resp.status_code == 200
    assert resp.json() == {"hidden": False}

    db = SessionLocal()
    try:
        assert app_settings.is_true(db, app_settings.KEY_LOCAL_LOGIN_HIDDEN) is False
    finally:
        db.close()


def test_local_login_endpoint_does_not_affect_login(client: httpx.Client) -> None:
    """The toggle is cosmetic (ADR 0020 Decision 4): /api/auth/login must keep working."""
    h = _admin_headers(client)
    client.put("/api/auth/local-login-visibility", json={"hidden": True}, headers=h)

    resp = client.post("/api/auth/login", json={"email": "admin@pantryos.local", "password": "TestPass123!"})
    assert resp.status_code == 200
    assert "token" in resp.json()
