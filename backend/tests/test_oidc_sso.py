"""Native OIDC SSO end-to-end tests (ADR 0020 Chunk C): identity mapping +
routes, exercised against the in-process fake IdP.

This duplicates the small fake-IdP fixture from ``test_oidc_protocol.py``
rather than importing it, to avoid pytest dual-import surprises when two test
modules both define fixtures/state at module scope (see task brief).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, unquote, urlparse

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core.ratelimit import login_limiter
from app.db import SessionLocal
from app.models import User
from app.services import app_settings, oidc
from modules.shopping.services import connector_config

FAKE_ISSUER = "http://fake-idp-sso.test"

_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
_KID = "test-key-sso-1"

# Test-controllable claims for the next /token response.
_CLAIMS_OVERRIDE: dict = {}

fake_idp_app = FastAPI()


@fake_idp_app.get("/.well-known/openid-configuration")
def _fake_discovery() -> dict:
    return {
        "issuer": FAKE_ISSUER,
        "authorization_endpoint": f"{FAKE_ISSUER}/authorize",
        "token_endpoint": f"{FAKE_ISSUER}/token",
        "jwks_uri": f"{FAKE_ISSUER}/jwks",
    }


@fake_idp_app.get("/jwks")
def _fake_jwks() -> dict:
    jwk = jwt.algorithms.RSAAlgorithm.to_jwk(_KEY.public_key(), as_dict=True)
    jwk["kid"] = _KID
    jwk["use"] = "sig"
    jwk["alg"] = "RS256"
    return {"keys": [jwk]}


@fake_idp_app.get("/authorize")
def _fake_authorize(request: Request):
    return RedirectResponse(url="/login-not-actually-used", status_code=302)


@fake_idp_app.post("/token")
def _fake_token() -> JSONResponse:
    now = datetime.now(timezone.utc)
    claims = {
        "iss": FAKE_ISSUER,
        "sub": "sso-user-1",
        "aud": "pantryos-test-client",
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=5)).timestamp()),
        "email": _CLAIMS_OVERRIDE.get("email", "member@example.lan"),
        "groups": _CLAIMS_OVERRIDE.get("groups", ["pantryos_users"]),
        "nonce": _CLAIMS_OVERRIDE.get("nonce", "unset"),
    }
    id_token = jwt.encode(claims, _KEY, algorithm="RS256", headers={"kid": _KID})
    return JSONResponse({"id_token": id_token, "access_token": "fake-access-token", "token_type": "Bearer"})


_fake_client = TestClient(fake_idp_app)


@pytest.fixture(autouse=True)
def _dispatch_httpx(monkeypatch, client: httpx.Client):
    backend_get = httpx.get
    backend_post = httpx.post

    def dispatch_get(url: str, *args, **kwargs):
        if url.startswith(FAKE_ISSUER):
            return _fake_client.get(url.replace(FAKE_ISSUER, ""), *args, **kwargs)
        return backend_get(url, *args, **kwargs)

    def dispatch_post(url: str, *args, **kwargs):
        if url.startswith(FAKE_ISSUER):
            return _fake_client.post(url.replace(FAKE_ISSUER, ""), *args, **kwargs)
        return backend_post(url, *args, **kwargs)

    monkeypatch.setattr(httpx, "get", dispatch_get)
    monkeypatch.setattr(httpx, "post", dispatch_post)

    _CLAIMS_OVERRIDE.clear()
    oidc._discovery_cache.clear()
    with oidc._state_lock:
        oidc._state_store.clear()
    login_limiter.record_success("oidc:testclient")
    yield
    login_limiter.record_success("oidc:testclient")


def _configure(db, **overrides) -> None:
    cfg = {
        "issuer": FAKE_ISSUER,
        "client_id": "pantryos-test-client",
        "client_secret": "test-secret",
    }
    cfg.update(overrides)
    connector_config.set_config(db, "oidc", cfg)


def _do_login(client: httpx.Client, *, claims: dict | None = None) -> httpx.Response:
    """Drive GET /login -> capture state -> GET /callback, as the browser would."""
    resp = client.get("/api/auth/oidc/login", follow_redirects=False)
    assert resp.status_code == 302
    parsed = urlparse(resp.headers["location"])
    params = {k: v[0] for k, v in parse_qs(parsed.query).items()}
    state = params["state"]

    db = SessionLocal()
    try:
        with oidc._state_lock:
            _, nonce, _ = oidc._state_store[state]
    finally:
        db.close()

    _CLAIMS_OVERRIDE.update(claims or {})
    _CLAIMS_OVERRIDE.setdefault("nonce", nonce)
    if "nonce" not in (claims or {}):
        _CLAIMS_OVERRIDE["nonce"] = nonce

    return client.get(
        "/api/auth/oidc/callback",
        params={"code": "fake-auth-code", "state": state},
        follow_redirects=False,
    )


def _fragment(resp: httpx.Response) -> str:
    parsed = urlparse(resp.headers["location"])
    # Every callback outcome must land where the SPA actually mounts LoginPage:
    # /admin (AdminGuard). There is no standalone /login route — the wildcard
    # would swallow it and the fragment handler would never run.
    assert parsed.path == "/admin", f"callback redirected to {parsed.path}, not /admin"
    return parsed.fragment


# ---------------------------------------------------------------------------
# 1. status + login 404 when unconfigured
# ---------------------------------------------------------------------------


def test_status_reflects_configured_state_and_login_404s_unconfigured(client: httpx.Client) -> None:
    status = client.get("/api/auth/oidc/status").json()
    assert status["enabled"] is False
    assert status["local_login_hidden"] is False

    assert client.get("/api/auth/oidc/login", follow_redirects=False).status_code == 404

    db = SessionLocal()
    try:
        _configure(db)
    finally:
        db.close()

    status = client.get("/api/auth/oidc/status").json()
    assert status["enabled"] is True
    assert status["button_label"] == "Sign in with SSO"


# ---------------------------------------------------------------------------
# 2. happy path: token authenticates /me and an admin route
# ---------------------------------------------------------------------------


def test_happy_path_token_authenticates_me_and_admin_route(client: httpx.Client) -> None:
    db = SessionLocal()
    try:
        _configure(db)
    finally:
        db.close()

    resp = _do_login(client, claims={"groups": ["pantryos_users", "pantryos_admin"]})
    assert resp.status_code == 302
    fragment = _fragment(resp)
    assert fragment.startswith("sso_token=")
    token = unquote(fragment.split("=", 1)[1])

    me = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert me.status_code == 200
    assert me.json()["email"] == "member@example.lan"
    assert me.json()["role"] == "admin"

    admin_resp = client.put(
        "/api/auth/local-login-visibility",
        json={"hidden": True},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert admin_resp.status_code == 200

    # Reset the toggle for other tests.
    client.put(
        "/api/auth/local-login-visibility",
        json={"hidden": False},
        headers={"Authorization": f"Bearer {token}"},
    )


# ---------------------------------------------------------------------------
# 3. group gate: not in pantryos_users -> sso_error, no user row
# ---------------------------------------------------------------------------


def test_group_gate_rejects_and_creates_no_user(client: httpx.Client) -> None:
    db = SessionLocal()
    try:
        _configure(db)
    finally:
        db.close()

    resp = _do_login(client, claims={"email": "outsider@example.lan", "groups": ["some_other_group"]})
    assert resp.status_code == 302
    assert _fragment(resp).startswith("sso_error=")

    db = SessionLocal()
    try:
        user = db.execute(select(User).where(User.email == "outsider@example.lan")).scalar_one_or_none()
        assert user is None
    finally:
        db.close()


# ---------------------------------------------------------------------------
# 4. role mapping + re-evaluation
# ---------------------------------------------------------------------------


def test_role_mapping_and_reevaluation_on_next_login(client: httpx.Client) -> None:
    db = SessionLocal()
    try:
        _configure(db)
    finally:
        db.close()

    resp = _do_login(client, claims={"email": "roletest@example.lan", "groups": ["pantryos_users", "pantryos_admin"]})
    assert resp.status_code == 302
    assert _fragment(resp).startswith("sso_token=")

    db = SessionLocal()
    try:
        user = db.execute(select(User).where(User.email == "roletest@example.lan")).scalar_one_or_none()
        assert user is not None
        assert user.role == "admin"
    finally:
        db.close()

    # Second login, no longer in the admin group: demoted.
    resp2 = _do_login(client, claims={"email": "roletest@example.lan", "groups": ["pantryos_users"]})
    assert resp2.status_code == 302
    assert _fragment(resp2).startswith("sso_token=")

    db = SessionLocal()
    try:
        user = db.execute(select(User).where(User.email == "roletest@example.lan")).scalar_one_or_none()
        assert user is not None
        assert user.role == "member"
    finally:
        db.close()


# ---------------------------------------------------------------------------
# 5. JIT idempotency, case-insensitive email match
# ---------------------------------------------------------------------------


def test_jit_idempotent_case_insensitive_email(client: httpx.Client) -> None:
    db = SessionLocal()
    try:
        _configure(db)
    finally:
        db.close()

    resp1 = _do_login(client, claims={"email": "User@X.com", "groups": ["pantryos_users"]})
    assert _fragment(resp1).startswith("sso_token=")

    resp2 = _do_login(client, claims={"email": "user@x.com", "groups": ["pantryos_users"]})
    assert _fragment(resp2).startswith("sso_token=")

    db = SessionLocal()
    try:
        rows = list(db.execute(select(User).where(User.email == "user@x.com")).scalars().all())
        assert len(rows) == 1
    finally:
        db.close()


# ---------------------------------------------------------------------------
# 7. rate limiting
# ---------------------------------------------------------------------------


def test_rate_limit_after_repeated_failures(client: httpx.Client) -> None:
    db = SessionLocal()
    try:
        _configure(db)
    finally:
        db.close()

    for _ in range(login_limiter.max_failures):
        resp = client.get(
            "/api/auth/oidc/callback",
            params={"code": "fake-auth-code", "state": "not-a-real-state"},
            follow_redirects=False,
        )
        assert resp.status_code == 302
        assert _fragment(resp).startswith("sso_error=")

    throttled = client.get(
        "/api/auth/oidc/callback",
        params={"code": "fake-auth-code", "state": "not-a-real-state"},
        follow_redirects=False,
    )
    assert throttled.status_code == 302
    fragment = unquote(_fragment(throttled))
    assert "sso_error=" in fragment
    assert "try again" in fragment.lower()


# ---------------------------------------------------------------------------
# 8. inactive user refused, not reactivated
# ---------------------------------------------------------------------------


def test_inactive_user_refused_not_reactivated(client: httpx.Client) -> None:
    db = SessionLocal()
    try:
        _configure(db)
    finally:
        db.close()

    resp = _do_login(client, claims={"email": "deactivate-me@example.lan", "groups": ["pantryos_users"]})
    assert _fragment(resp).startswith("sso_token=")

    db = SessionLocal()
    try:
        user = db.execute(select(User).where(User.email == "deactivate-me@example.lan")).scalar_one_or_none()
        assert user is not None
        user.is_active = False
        db.add(user)
        db.commit()
    finally:
        db.close()

    resp2 = _do_login(client, claims={"email": "deactivate-me@example.lan", "groups": ["pantryos_users"]})
    assert resp2.status_code == 302
    assert _fragment(resp2).startswith("sso_error=")

    db = SessionLocal()
    try:
        user = db.execute(select(User).where(User.email == "deactivate-me@example.lan")).scalar_one_or_none()
        assert user is not None
        assert user.is_active is False
    finally:
        db.close()


# ---------------------------------------------------------------------------
# 9. break-glass: local login works with OIDC enabled + local login hidden
# ---------------------------------------------------------------------------


def test_local_login_still_works_when_oidc_enabled_and_local_login_hidden(client: httpx.Client) -> None:
    db = SessionLocal()
    try:
        _configure(db)
        app_settings.set_setting(db, app_settings.KEY_LOCAL_LOGIN_HIDDEN, "true")
    finally:
        db.close()

    email = "break-glass-admin@example.com"
    password = "CorrectHorse9!"
    boot = client.post("/api/auth/bootstrap-admin", json={"email": email, "password": password})
    assert boot.status_code in {200, 409}

    login = client.post("/api/auth/login", json={"email": email, "password": password})
    assert login.status_code == 200
    assert login.json()["token"]

    db = SessionLocal()
    try:
        app_settings.set_setting(db, app_settings.KEY_LOCAL_LOGIN_HIDDEN, "false")
    finally:
        db.close()
