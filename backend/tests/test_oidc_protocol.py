"""OIDC protocol core (ADR 0020 Chunk B): discovery/JWKS, state+PKCE, the
authorize-URL builder, token exchange, and ID-token verification.

Builds a small in-process fake IdP (FastAPI app + TestClient), the way
``test_activation_e2e.py`` builds the in-process cloud license server, and
dispatches ``httpx.get``/``httpx.post`` by URL prefix: fake-issuer traffic
goes to the fake IdP's TestClient, everything else goes to the normal
in-process backend client that ``conftest.py`` already wires up.

No routes are exercised here (Chunk C owns those) — everything is driven
directly against ``app.services.oidc``.
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlparse

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.testclient import TestClient

from app.db import SessionLocal
from app.services import oidc
from modules.shopping.services import connector_config

FAKE_ISSUER = "http://fake-idp.test"

# --------------------------------------------------------------------------
# Fake IdP: a second FastAPI app with its own RSA key, generated at import.
# --------------------------------------------------------------------------

_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
_KID = "test-key-1"

_ROGUE_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)

# The IdP's CURRENT signing key — tests swap this to simulate key rotation
# (the fixture resets it). /jwks and /token both read it.
_CURRENT = {"key": _KEY, "kid": _KID}

# Test-controllable claims used to build the next id_token issued by /token.
# Tests mutate this dict (or pass overrides) before hitting the callback flow.
_CLAIMS_OVERRIDE: dict = {}

_discovery_hits = {"count": 0}
_authorize_requests: list[dict] = []

fake_idp_app = FastAPI()


@fake_idp_app.get("/.well-known/openid-configuration")
def _fake_discovery() -> dict:
    _discovery_hits["count"] += 1
    return {
        "issuer": FAKE_ISSUER,
        "authorization_endpoint": f"{FAKE_ISSUER}/authorize",
        "token_endpoint": f"{FAKE_ISSUER}/token",
        "jwks_uri": f"{FAKE_ISSUER}/jwks",
    }


@fake_idp_app.get("/jwks")
def _fake_jwks() -> dict:
    jwk = jwt.algorithms.RSAAlgorithm.to_jwk(_CURRENT["key"].public_key(), as_dict=True)
    jwk["kid"] = _CURRENT["kid"]
    jwk["use"] = "sig"
    jwk["alg"] = "RS256"
    return {"keys": [jwk]}


@fake_idp_app.get("/authorize")
def _fake_authorize(request: Request):
    _authorize_requests.append(dict(request.query_params))
    return RedirectResponse(url="/login-not-actually-used", status_code=302)


@fake_idp_app.post("/token")
def _fake_token() -> JSONResponse:
    now = datetime.now(timezone.utc)
    claims = {
        "iss": FAKE_ISSUER,
        "sub": "user-123",
        "aud": _CLAIMS_OVERRIDE.get("aud", "pantryos-test-client"),
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=5)).timestamp()),
        "email": "member@example.lan",
        "nonce": _CLAIMS_OVERRIDE.get("nonce", "unset"),
    }
    claims.update(_CLAIMS_OVERRIDE.get("extra", {}))
    for key in ("iss", "aud", "exp"):
        if key in _CLAIMS_OVERRIDE:
            claims[key] = _CLAIMS_OVERRIDE[key]

    signing_key = _ROGUE_KEY if _CLAIMS_OVERRIDE.get("wrong_signer") else _CURRENT["key"]
    headers = {} if _CLAIMS_OVERRIDE.get("no_kid") else {"kid": _CURRENT["kid"]}
    id_token = jwt.encode(claims, signing_key, algorithm="RS256", headers=headers)
    return JSONResponse({"id_token": id_token, "access_token": "fake-access-token", "token_type": "Bearer"})


_fake_client = TestClient(fake_idp_app)


@pytest.fixture(autouse=True)
def _dispatch_httpx(monkeypatch, client: httpx.Client):
    """Route fake-issuer traffic to the fake IdP; everything else to the backend."""
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

    _discovery_hits["count"] = 0
    _authorize_requests.clear()
    _CLAIMS_OVERRIDE.clear()
    _CURRENT["key"], _CURRENT["kid"] = _KEY, _KID
    # Also clear the module's in-process caches so tests don't bleed into each
    # other via the 15-minute discovery cache / state store.
    oidc._discovery_cache.clear()
    with oidc._state_lock:
        oidc._state_store.clear()
    yield


class _FakeRequest:
    """Minimal stand-in for fastapi.Request; oidc only reads .base_url."""

    base_url = "http://pantryos.local/"


def _configure(db, **overrides) -> None:
    cfg = {
        "issuer": FAKE_ISSUER,
        "client_id": "pantryos-test-client",
        "client_secret": "test-secret",
    }
    cfg.update(overrides)
    connector_config.set_config(db, "oidc", cfg)


def _begin_and_capture_pkce(db) -> tuple[str, str, str]:
    """Call begin_login, then read back the state/nonce/verifier that
    begin_login stored (by peeking at the module-level store) plus the
    authorize URL's state/code_challenge for cross-checking."""
    url = oidc.begin_login(db, _FakeRequest())
    parsed = urlparse(url)
    params = {k: v[0] for k, v in parse_qs(parsed.query).items()}
    state = params["state"]
    with oidc._state_lock:
        code_verifier, nonce, _ = oidc._state_store[state]
    return state, code_verifier, nonce, params


def _run_full_login(db, *, claims_override: dict | None = None) -> dict:
    """Drive begin_login -> fake /authorize -> fake /token -> verify, as
    Chunk C's callback route will, and return the verified claims."""
    if claims_override:
        _CLAIMS_OVERRIDE.update(claims_override)

    url = oidc.begin_login(db, _FakeRequest())
    parsed = urlparse(url)
    params = {k: v[0] for k, v in parse_qs(parsed.query).items()}
    state = params["state"]

    if "nonce" not in (claims_override or {}):
        with oidc._state_lock:
            _, nonce, _ = oidc._state_store[state]
        _CLAIMS_OVERRIDE["nonce"] = nonce

    return oidc._exchange_and_verify(db, _FakeRequest(), code="fake-auth-code", state=state)


# --------------------------------------------------------------------------
# begin_login
# --------------------------------------------------------------------------


def test_begin_login_raises_when_unconfigured() -> None:
    db = SessionLocal()
    try:
        with pytest.raises(oidc.OidcError):
            oidc.begin_login(db, _FakeRequest())
    finally:
        db.close()


def test_begin_login_url_has_required_params_and_s256_challenge() -> None:
    db = SessionLocal()
    try:
        _configure(db)
        state, code_verifier, nonce, params = _begin_and_capture_pkce(db)

        assert params["response_type"] == "code"
        assert params["client_id"] == "pantryos-test-client"
        assert params["redirect_uri"] == "http://pantryos.local/api/auth/oidc/callback"
        assert params["scope"] == "openid profile email groups"
        assert params["state"] == state
        assert params["nonce"] == nonce
        assert params["code_challenge_method"] == "S256"
        assert params["code_challenge"] == oidc._code_challenge(code_verifier)
        # PKCE challenge must not just echo the verifier.
        assert params["code_challenge"] != code_verifier
    finally:
        db.close()


# --------------------------------------------------------------------------
# Happy path
# --------------------------------------------------------------------------


def test_happy_path_returns_verified_claims() -> None:
    db = SessionLocal()
    try:
        _configure(db)
        claims = _run_full_login(db)
        assert claims["email"] == "member@example.lan"
        assert claims["aud"] == "pantryos-test-client"
        assert claims["iss"] == FAKE_ISSUER
    finally:
        db.close()


# --------------------------------------------------------------------------
# Security failures
# --------------------------------------------------------------------------


def test_wrong_audience_raises() -> None:
    db = SessionLocal()
    try:
        _configure(db)
        with pytest.raises(oidc.OidcError):
            _run_full_login(db, claims_override={"aud": "someone-else"})
    finally:
        db.close()


def test_wrong_issuer_raises() -> None:
    db = SessionLocal()
    try:
        _configure(db)
        with pytest.raises(oidc.OidcError):
            _run_full_login(db, claims_override={"iss": "http://not-the-idp.test"})
    finally:
        db.close()


def test_expired_id_token_raises() -> None:
    db = SessionLocal()
    try:
        _configure(db)
        past = int((datetime.now(timezone.utc) - timedelta(minutes=5)).timestamp())
        with pytest.raises(oidc.OidcError):
            _run_full_login(db, claims_override={"exp": past})
    finally:
        db.close()


def test_wrong_signer_key_raises() -> None:
    db = SessionLocal()
    try:
        _configure(db)
        with pytest.raises(oidc.OidcError):
            _run_full_login(db, claims_override={"wrong_signer": True})
    finally:
        db.close()


def test_replayed_state_raises_on_second_use() -> None:
    db = SessionLocal()
    try:
        _configure(db)
        url = oidc.begin_login(db, _FakeRequest())
        parsed = urlparse(url)
        params = {k: v[0] for k, v in parse_qs(parsed.query).items()}
        state = params["state"]
        with oidc._state_lock:
            _, nonce, _ = oidc._state_store[state]
        _CLAIMS_OVERRIDE["nonce"] = nonce

        claims = oidc._exchange_and_verify(db, _FakeRequest(), code="fake-auth-code", state=state)
        assert claims["email"] == "member@example.lan"

        with pytest.raises(oidc.OidcError):
            oidc._exchange_and_verify(db, _FakeRequest(), code="fake-auth-code", state=state)
    finally:
        db.close()


def test_tampered_nonce_raises() -> None:
    db = SessionLocal()
    try:
        _configure(db)
        with pytest.raises(oidc.OidcError):
            _run_full_login(db, claims_override={"nonce": "not-the-real-nonce"})
    finally:
        db.close()


# --------------------------------------------------------------------------
# Discovery cache
# --------------------------------------------------------------------------


def test_discovery_cache_hits_once_then_config_change_rediscovers() -> None:
    db = SessionLocal()
    try:
        _configure(db)
        oidc.begin_login(db, _FakeRequest())
        assert _discovery_hits["count"] == 1

        # Same config: cached, no new discovery hit.
        oidc.begin_login(db, _FakeRequest())
        assert _discovery_hits["count"] == 1

        # Config change (new client_id): cache key changes, forces rediscovery.
        _configure(db, client_id="a-different-client-id")
        oidc.begin_login(db, _FakeRequest())
        assert _discovery_hits["count"] == 2
    finally:
        db.close()


def test_idp_key_rotation_inside_cache_ttl_triggers_jwks_refetch() -> None:
    """An ID token with an unknown kid forces one cache-bypassing JWKS refetch
    (IdP rotated keys mid-TTL) instead of failing until the cache expires."""
    db = SessionLocal()
    try:
        _configure(db)
        claims = _run_full_login(db)  # caches JWKS for test-key-1
        assert claims["email"] == "member@example.lan"

        _CURRENT["key"] = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        _CURRENT["kid"] = "test-key-2"

        _CLAIMS_OVERRIDE.clear()
        claims = _run_full_login(db)  # new kid vs stale cache -> forced refetch
        assert claims["email"] == "member@example.lan"

        # A token from a signer that is in NO jwks still fails even after the
        # forced refetch (the retry is once, then the generic error).
        _CLAIMS_OVERRIDE.clear()
        with pytest.raises(oidc.OidcError):
            _run_full_login(db, claims_override={"wrong_signer": True, "no_kid": False})
    finally:
        db.close()
