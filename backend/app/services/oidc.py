"""Native OIDC SSO config resolution and protocol core (ADR 0020).

Chunk A: config plumbing — ``effective_config``, ``enabled``, ``redirect_uri``.
Chunk B (this file, in addition): discovery + JWKS fetch/cache, the state/PKCE
store, the authorize-URL builder (``begin_login``), token exchange, and
ID-token verification (``_exchange_and_verify``). Claims -> ``User`` mapping
and ``AuthToken`` minting (``complete_login`` proper) land in Chunk C; the
placeholder below intentionally stops at "verified claims dict".

Config itself is DB-only (ADR 0009 connector-config pattern, key "oidc") — no
new environment variables, per ADR 0020 Decision 5.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import secrets
import threading
import time
from urllib.parse import urlencode

import httpx
import jwt
from fastapi import Request
from sqlalchemy.orm import Session

from modules.shopping.services import connector_config

logger = logging.getLogger(__name__)

DEFAULTS = {
    "scopes": "openid profile email groups",
    "groups_claim": "groups",
    "users_group": "pantryos_users",
    "admin_group": "pantryos_admin",
    "button_label": "Sign in with SSO",
}

# All fields the "oidc" connector spec declares (see connector_config._SPECS).
# effective_config() always returns exactly these keys so downstream code can
# index without .get() (Chunk A handoff note).
_SPEC_FIELDS = (
    "issuer",
    "client_id",
    "client_secret",
    "scopes",
    "groups_claim",
    "users_group",
    "admin_group",
    "button_label",
)

# Fixed algorithm allowlist for ID-token verification. Never add none/HS256.
_ALLOWED_ALGORITHMS = ["RS256", "ES256", "EdDSA"]

_GENERIC_VERIFY_ERROR = "The identity provider's login response could not be verified."


class OidcError(Exception):
    """Raised for any SSO failure; the message is safe to show the user."""


def effective_config(db: Session) -> dict[str, str]:
    """Effective OIDC config: DB override, with DEFAULTS filled in for blanks.

    Always returns all 8 spec'd keys (blank string when unset, DEFAULTS where
    defined) so callers can index (``cfg["issuer"]``) without ``.get``.
    """
    cfg = connector_config.get_effective(db, "oidc")
    merged: dict[str, str] = {name: cfg.get(name, "") or "" for name in _SPEC_FIELDS}
    for key, default in DEFAULTS.items():
        if not merged.get(key):
            merged[key] = default
    return merged


def enabled(db: Session) -> bool:
    """SSO is usable once issuer, client_id, and client_secret are all set."""
    cfg = effective_config(db)
    return bool(cfg.get("issuer") and cfg.get("client_id") and cfg.get("client_secret"))


def redirect_uri(request: Request) -> str:
    """Single-origin redirect URI, shown read-only in the admin UI for IdP registration."""
    return f"{request.base_url}api/auth/oidc/callback"


# --------------------------------------------------------------------------
# Discovery + JWKS cache.
#
# In-process, 15-minute TTL, keyed by (issuer, client_id, config hash) so a
# config change (even one that doesn't touch issuer/client_id) is treated as
# a different cache entry and forces rediscovery.
# --------------------------------------------------------------------------

_DISCOVERY_TTL_SECONDS = 15 * 60

# cache_key -> (discovery_doc, jwks, expires_at_monotonic)
_discovery_cache: dict[tuple[str, str, str], tuple[dict, dict, float]] = {}
_discovery_lock = threading.Lock()


def _config_hash(cfg: dict[str, str]) -> str:
    return hashlib.sha256(json.dumps(cfg, sort_keys=True).encode("utf-8")).hexdigest()


def _normalize_issuer(issuer: str) -> str:
    return issuer.rstrip("/")


def _discovery_cache_key(cfg: dict[str, str]) -> tuple[str, str, str]:
    return (_normalize_issuer(cfg["issuer"]), cfg["client_id"], _config_hash(cfg))


def _fetch_discovery(cfg: dict[str, str], *, force: bool = False) -> tuple[dict, dict]:
    """Return (discovery_doc, jwks), using the in-process cache when fresh.

    ``force=True`` bypasses the cache — used once per callback when an ID
    token carries a ``kid`` the cached JWKS doesn't know (IdP key rotation
    inside the TTL window)."""
    key = _discovery_cache_key(cfg)
    now = time.monotonic()

    if not force:
        with _discovery_lock:
            cached = _discovery_cache.get(key)
            if cached is not None and cached[2] > now:
                return cached[0], cached[1]

    issuer = _normalize_issuer(cfg["issuer"])
    try:
        disco_resp = httpx.get(f"{issuer}/.well-known/openid-configuration", timeout=10.0)
        disco_resp.raise_for_status()
        doc = disco_resp.json()
        jwks_resp = httpx.get(doc["jwks_uri"], timeout=10.0)
        jwks_resp.raise_for_status()
        jwks = jwks_resp.json()
    except Exception:
        logger.exception("OIDC discovery or JWKS fetch failed for issuer %s", issuer)
        raise OidcError("Could not reach the identity provider.")

    with _discovery_lock:
        _discovery_cache[key] = (doc, jwks, now + _DISCOVERY_TTL_SECONDS)
    return doc, jwks


# --------------------------------------------------------------------------
# State + PKCE store.
#
# In-process on purpose (same rationale as backend/app/core/ratelimit.py: a
# household instance is a single backend process, so a shared store would be
# overkill). A restart mid-login just means the user clicks the button again.
# --------------------------------------------------------------------------

_STATE_TTL_SECONDS = 10 * 60

# state -> (code_verifier, nonce, created_at_monotonic)
_state_store: dict[str, tuple[str, str, float]] = {}
_state_lock = threading.Lock()

_LOGIN_EXPIRED_ERROR = "Your login attempt expired or was already used. Please try signing in again."


def _prune_states_locked(now: float) -> None:
    expired = [s for s, (_, _, created_at) in _state_store.items() if now - created_at > _STATE_TTL_SECONDS]
    for s in expired:
        del _state_store[s]


def _store_state(state: str, code_verifier: str, nonce: str) -> None:
    now = time.monotonic()
    with _state_lock:
        _prune_states_locked(now)
        _state_store[state] = (code_verifier, nonce, now)


def _pop_state(state: str) -> tuple[str, str]:
    """Single-use lookup: pops the entry so a replayed state always fails."""
    now = time.monotonic()
    with _state_lock:
        entry = _state_store.pop(state, None)
    if entry is None:
        raise OidcError(_LOGIN_EXPIRED_ERROR)
    code_verifier, nonce, created_at = entry
    if now - created_at > _STATE_TTL_SECONDS:
        raise OidcError(_LOGIN_EXPIRED_ERROR)
    return code_verifier, nonce


def _code_challenge(code_verifier: str) -> str:
    digest = hashlib.sha256(code_verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


# --------------------------------------------------------------------------
# Authorization Code + PKCE flow.
# --------------------------------------------------------------------------


def begin_login(db: Session, request: Request) -> str:
    """Build the authorize URL and stash the state/nonce/verifier for the callback."""
    if not enabled(db):
        raise OidcError("SSO is not configured.")

    cfg = effective_config(db)
    doc, _jwks = _fetch_discovery(cfg)

    state = secrets.token_urlsafe(32)
    nonce = secrets.token_urlsafe(32)
    code_verifier = secrets.token_urlsafe(32)
    code_challenge = _code_challenge(code_verifier)

    _store_state(state, code_verifier, nonce)

    params = {
        "response_type": "code",
        "client_id": cfg["client_id"],
        "redirect_uri": redirect_uri(request),
        "scope": cfg["scopes"],
        "state": state,
        "nonce": nonce,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
    }
    authorization_endpoint = doc["authorization_endpoint"]
    return f"{authorization_endpoint}?{urlencode(params)}"


def _select_jwk(id_token: str, jwks: dict) -> dict:
    keys = jwks.get("keys") or []
    try:
        header = jwt.get_unverified_header(id_token)
    except Exception:
        raise OidcError(_GENERIC_VERIFY_ERROR)

    kid = header.get("kid")
    if kid:
        for candidate in keys:
            if candidate.get("kid") == kid:
                return candidate
        raise OidcError(_GENERIC_VERIFY_ERROR)

    if len(keys) == 1:
        return keys[0]

    raise OidcError(_GENERIC_VERIFY_ERROR)


def _verify_id_token(
    id_token: str, jwks: dict, client_id: str, issuer: str, expected_nonce: str, cfg: dict[str, str] | None = None
) -> dict:
    try:
        jwk_dict = _select_jwk(id_token, jwks)
    except OidcError:
        # Unknown kid inside the cache TTL usually means the IdP rotated its
        # signing key: refetch the JWKS once (cache bypassed) and retry
        # selection before giving up.
        if cfg is None:
            raise
        _, fresh_jwks = _fetch_discovery(cfg, force=True)
        jwk_dict = _select_jwk(id_token, fresh_jwks)
    try:
        signing_key = jwt.PyJWK(jwk_dict).key
        claims = jwt.decode(
            id_token,
            key=signing_key,
            algorithms=_ALLOWED_ALGORITHMS,
            audience=client_id,
            issuer=issuer,
        )
    except OidcError:
        raise
    except Exception:
        logger.exception("OIDC ID token verification failed")
        raise OidcError(_GENERIC_VERIFY_ERROR)

    if claims.get("nonce") != expected_nonce:
        raise OidcError(_GENERIC_VERIFY_ERROR)

    return claims


def _exchange_and_verify(db: Session, request: Request, code: str, state: str) -> dict:
    """Pop the state, exchange the code for tokens, and verify the ID token.

    Returns the verified claims dict. Identity mapping (claims -> User,
    AuthToken minting) is Chunk C's job — this stops at "verified claims".
    """
    code_verifier, nonce = _pop_state(state)
    cfg = effective_config(db)
    doc, jwks = _fetch_discovery(cfg)

    data = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect_uri(request),
        "client_id": cfg["client_id"],
        "client_secret": cfg["client_secret"],
        "code_verifier": code_verifier,
    }
    try:
        resp = httpx.post(doc["token_endpoint"], data=data, timeout=10.0)
    except Exception:
        logger.exception("OIDC token exchange request failed")
        raise OidcError("The identity provider rejected the login.")

    if resp.status_code != 200:
        logger.warning("OIDC token exchange rejected: status=%s body=%s", resp.status_code, resp.text)
        raise OidcError("The identity provider rejected the login.")

    try:
        id_token = resp.json()["id_token"]
    except Exception:
        logger.exception("OIDC token response missing id_token")
        raise OidcError("The identity provider rejected the login.")

    return _verify_id_token(id_token, jwks, cfg["client_id"], doc["issuer"], nonce, cfg=cfg)


def _groups_from_claims(cfg: dict[str, str], claims: dict) -> list[str]:
    groups = claims.get(cfg["groups_claim"]) or []
    if isinstance(groups, str):
        return [groups]
    return list(groups)


def complete_login(db: Session, request: Request, code: str, state: str) -> tuple["User", "AuthToken"]:
    """Claims -> User mapping + AuthToken minting (ADR 0020 II.2, Chunk C)."""
    from app.models import User
    from app.services.auth import _password_record, mint_token

    claims = _exchange_and_verify(db, request, code, state)
    cfg = effective_config(db)

    email = (claims.get("email") or "").lower().strip()
    if not email:
        raise OidcError("The identity provider did not supply an email address.")

    groups = _groups_from_claims(cfg, claims)
    if cfg["users_group"] not in groups:
        raise OidcError("This account is not in the PantryOS users group.")

    role = "admin" if cfg["admin_group"] in groups else "member"

    from sqlalchemy import select

    user = db.execute(select(User).where(User.email == email)).scalar_one_or_none()
    if user is None:
        user = User(
            email=email,
            password_hash=_password_record(secrets.token_urlsafe(32)),
            role=role,
            is_active=True,
            is_provisioned=True,
        )
        db.add(user)
        db.commit()
        db.refresh(user)
    else:
        if not user.is_active:
            raise OidcError("This account has been deactivated.")
        if user.role != role:
            user.role = role
            db.add(user)
            db.commit()
            db.refresh(user)

    return user, mint_token(db, user)
