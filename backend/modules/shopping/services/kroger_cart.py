"""Kroger Cart (OUT) fulfillment — Phase 4 of ADR 0013.

The Cart API is the one self-serve vendor ordering path, but unlike product
search (which uses client_credentials) it needs a USER-authorized token: a
household member authorizes our app once via OAuth2 authorization_code (scope
``cart.basic:write``), and we store the resulting access+refresh tokens
encrypted, reusing the ADR 0009 connector-config crypto. Pushing the buy list
then PUTs ``{upc, quantity}`` items into that user's Kroger cart for checkout.

Design notes:
- Tokens live in ``connector_config`` rows under keys kept OUT of that module's
  ``_SPECS``, so they never appear in the admin config view or accept manual
  edits — this module owns their lifecycle (issue/refresh/revoke).
- ``get_access_token`` refreshes transparently when the access token is within a
  minute of expiry; ``add_to_cart`` also refreshes once on a 401.
"""

from __future__ import annotations

import base64
import time
from dataclasses import dataclass
from urllib.parse import urlencode, urlparse

import httpx
from sqlalchemy.orm import Session

from app.core.crypto import decrypt_json, encrypt_json, secrets_available
from app.models import ConnectorConfig

_AUTHORIZE_URL = "https://api.kroger.com/v1/connect/oauth2/authorize"
_TOKEN_URL = "https://api.kroger.com/v1/connect/oauth2/token"
_CART_ADD_URL = "https://api.kroger.com/v1/cart/add"
_SCOPE = "cart.basic:write"
_TIMEOUT = 8.0

# ConnectorConfig row keys (encrypted). Deliberately absent from
# connector_config._SPECS so tokens never surface in the admin config view.
_TOKEN_KEY = "kroger_cart_token"
_STATE_KEY = "kroger_cart_oauth_state"

# Refresh a little before expiry so an add doesn't race the clock.
_EXPIRY_SKEW_SECONDS = 60


class KrogerCartError(RuntimeError):
    """Cart push / OAuth failure with a human-readable reason."""


class KrogerNotConnected(RuntimeError):
    """No user has authorized the Kroger cart yet (or the link was revoked)."""


@dataclass(frozen=True)
class CartItem:
    upc: str
    quantity: int


# ---- encrypted row store (ConnectorConfig, bypassing the _SPECS view) -------


def _read_row(db: Session, key: str) -> dict:
    row = db.get(ConnectorConfig, key)
    if row is None:
        return {}
    try:
        return decrypt_json(row.ciphertext)
    except Exception:
        # Lost/rotated CONFIG_SECRET_KEY — treat as no data (forces reconnect).
        return {}


def _write_row(db: Session, key: str, payload: dict) -> None:
    row = db.get(ConnectorConfig, key)
    ciphertext = encrypt_json(payload)
    if row is None:
        db.add(ConnectorConfig(key=key, ciphertext=ciphertext))
    else:
        row.ciphertext = ciphertext
    db.commit()


def _delete_row(db: Session, key: str) -> None:
    row = db.get(ConnectorConfig, key)
    if row is not None:
        db.delete(row)
        db.commit()


# ---- OAuth authorization-code flow -----------------------------------------


def build_authorize_url(*, client_id: str, redirect_uri: str, state: str) -> str:
    """The Kroger consent URL the household member is sent to."""
    query = urlencode(
        {
            "scope": _SCOPE,
            "response_type": "code",
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "state": state,
        }
    )
    return f"{_AUTHORIZE_URL}?{query}"


def save_oauth_state(db: Session, state: str) -> None:
    _write_row(db, _STATE_KEY, {"state": state})


def verify_and_consume_state(db: Session, state: str) -> bool:
    """One-shot CSRF check: the callback's state must match what we issued."""
    stored = _read_row(db, _STATE_KEY).get("state")
    _delete_row(db, _STATE_KEY)
    return bool(stored) and bool(state) and stored == state


def _basic_auth(client_id: str, client_secret: str) -> str:
    return base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()


def _store_token_response(db: Session, body: dict) -> None:
    payload = {
        "access_token": body["access_token"],
        "refresh_token": body.get("refresh_token", ""),
        "expires_at": time.time() + float(body.get("expires_in", 1800)),
        "scope": body.get("scope", _SCOPE),
    }
    # Kroger omits the refresh_token on a refresh response — keep the prior one.
    if not payload["refresh_token"]:
        payload["refresh_token"] = _read_row(db, _TOKEN_KEY).get("refresh_token", "")
    _write_row(db, _TOKEN_KEY, payload)


def exchange_code(db: Session, *, code: str, client_id: str, client_secret: str, redirect_uri: str) -> None:
    resp = httpx.post(
        _TOKEN_URL,
        data={"grant_type": "authorization_code", "code": code, "redirect_uri": redirect_uri},
        headers={
            "Authorization": f"Basic {_basic_auth(client_id, client_secret)}",
            "Content-Type": "application/x-www-form-urlencoded",
        },
        timeout=_TIMEOUT,
    )
    if resp.status_code >= 400:
        raise KrogerCartError(f"Token exchange failed ({resp.status_code}): {resp.text[:200]}")
    _store_token_response(db, resp.json())


def _refresh(db: Session, *, refresh_token: str, client_id: str, client_secret: str) -> None:
    resp = httpx.post(
        _TOKEN_URL,
        data={"grant_type": "refresh_token", "refresh_token": refresh_token},
        headers={
            "Authorization": f"Basic {_basic_auth(client_id, client_secret)}",
            "Content-Type": "application/x-www-form-urlencoded",
        },
        timeout=_TIMEOUT,
    )
    if resp.status_code >= 400:
        raise KrogerNotConnected("Kroger session expired — reconnect the account.")
    _store_token_response(db, resp.json())


def get_access_token(db: Session, *, client_id: str, client_secret: str) -> str:
    bundle = _read_row(db, _TOKEN_KEY)
    if not bundle.get("access_token"):
        raise KrogerNotConnected("No Kroger account is connected.")
    if float(bundle.get("expires_at", 0)) <= time.time() + _EXPIRY_SKEW_SECONDS:
        refresh_token = bundle.get("refresh_token")
        if not refresh_token:
            raise KrogerNotConnected("Kroger session expired — reconnect the account.")
        _refresh(db, refresh_token=refresh_token, client_id=client_id, client_secret=client_secret)
        bundle = _read_row(db, _TOKEN_KEY)
    return bundle["access_token"]


def is_connected(db: Session) -> bool:
    return bool(_read_row(db, _TOKEN_KEY).get("access_token"))


def connection_status(db: Session) -> dict:
    bundle = _read_row(db, _TOKEN_KEY)
    return {
        "connected": bool(bundle.get("access_token")),
        "expires_at": bundle.get("expires_at"),
        "scope": bundle.get("scope"),
        "secrets_available": secrets_available(),
    }


def disconnect(db: Session) -> None:
    _delete_row(db, _TOKEN_KEY)


def app_origin(redirect_uri: str) -> str:
    """The scheme://host of the redirect URI — where the callback bounces the
    browser back to (the SPA's integrations page)."""
    parsed = urlparse(redirect_uri)
    if not parsed.scheme or not parsed.netloc:
        return ""
    return f"{parsed.scheme}://{parsed.netloc}"


# ---- Cart push --------------------------------------------------------------


def _cart_error_reason(resp: "httpx.Response") -> str:
    """Human-readable reason from a Kroger cart error body."""
    try:
        errors = resp.json().get("errors") or {}
        return str(errors.get("reason") or errors.get("code") or resp.text[:120])
    except Exception:
        return (resp.text or "").strip()[:120] or f"HTTP {resp.status_code}"


def add_to_cart_via(items: list[CartItem], *, get_token) -> list[dict]:
    """PUT the items into the user's Kroger cart using ``get_token(force)``.

    Token acquisition is injected so the same cart logic serves both the local
    BYO-credential path and the brokered path (ADR 0018 §3). Refreshes the
    token once (``get_token(force=True)``) on a 401.

    Kroger's cart-add is all-or-nothing, so a single un-orderable UPC would
    otherwise sink the whole export. On a 400 we retry the items one at a time to
    isolate the rejects and return them (``[{upc, reason}]``) so the good items
    still land in the cart. Auth/5xx failures still raise."""
    if not items:
        return []

    def _put(token: str, batch: list[CartItem]) -> httpx.Response:
        return httpx.put(
            _CART_ADD_URL,
            json={"items": [{"upc": it.upc, "quantity": it.quantity} for it in batch]},
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            timeout=_TIMEOUT,
        )

    token = get_token(False)
    resp = _put(token, items)
    if resp.status_code == 401:  # token rejected — refresh once and retry
        token = get_token(True)
        resp = _put(token, items)

    if resp.status_code in (200, 204):
        return []
    if resp.status_code != 400:  # auth/server error — not a per-item problem
        raise KrogerCartError(f"Kroger cart add failed ({resp.status_code}): {resp.text[:200]}")

    # A bad item in the batch — isolate it so the rest still get added.
    failed: list[dict] = []
    for item in items:
        one = _put(token, [item])
        if one.status_code not in (200, 204):
            failed.append({"upc": item.upc, "reason": _cart_error_reason(one)})
    return failed


def add_to_cart(db: Session, items: list[CartItem], *, client_id: str, client_secret: str) -> list[dict]:
    """Local BYO-credential path: tokens from connector_config, refreshed here."""

    def _local_token(force: bool) -> str:
        if force:
            refresh_token = _read_row(db, _TOKEN_KEY).get("refresh_token")
            if refresh_token:
                _refresh(db, refresh_token=refresh_token, client_id=client_id, client_secret=client_secret)
            return _read_row(db, _TOKEN_KEY).get("access_token", "")
        return get_access_token(db, client_id=client_id, client_secret=client_secret)

    return add_to_cart_via(items, get_token=_local_token)
