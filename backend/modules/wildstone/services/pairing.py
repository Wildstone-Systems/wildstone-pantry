"""Pairing with Wildstone: one code instead of two copied secrets (ADR 0026 §7).

1. An admin asks for a code. It is short enough to type, lives five minutes,
   and works once.
2. The admin types it, with PantryOS's address, into Wildstone.
3. Keystone calls :func:`exchange` once with the code, Keystone's own address
   and a publisher token it just minted. PantryOS stores the connection,
   mints an agent key for Wildstone's adapter, and returns that key.

Neither secret is ever shown to a person, and each crosses the network once,
machine to machine. Pairing again rotates both: the previous paired agent key
is revoked and the connection's token replaced.

Codes are held in memory, hashed, like device pairing (ADR 0018 §8): a restart
simply voids an outstanding code. Only one code is outstanding at a time;
asking for another replaces it.
"""

from __future__ import annotations

import hashlib
import secrets
import threading
import time
import uuid
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from app.services.agent_keys import create_agent_key, revoke_agent_key
from app.services.app_settings import get_setting, set_setting
from app.services.auth import get_user
from modules.wildstone.services import connection, web_url
from modules.wildstone.services.contract import CONTRACT_VERSION

PAIRING_TTL_SECONDS = 300
PAIRED_KEY_SETTING = "wildstone_paired_agent_key_id"
PAIRED_KEY_NAME = "Wildstone (paired)"
PAIRED_AT_SETTING = "wildstone_paired_at"

# No 0/O, 1/I/L: the code is read off one screen and typed into another.
_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"

# code hash -> (user id, expires at, monotonic). One outstanding code.
_pending: dict[str, tuple[str, float]] = {}
_lock = threading.Lock()


class PairingRejected(Exception):
    """The exchange was refused. The message is safe to show the caller.

    ``bad_code`` separates "that code is no good" (wrong, expired, used — all
    answered alike) from a malformed request.
    """

    def __init__(self, message: str, *, bad_code: bool = False):
        super().__init__(message)
        self.bad_code = bad_code


def _normalise(code: str) -> str:
    return "".join(ch for ch in (code or "").upper() if ch.isalnum())


def _hash(code: str) -> str:
    return hashlib.sha256(_normalise(code).encode()).hexdigest()


def mint_code(user_id: uuid.UUID) -> tuple[str, int]:
    """A new pairing code, formatted ABCD-EFGH. Returns (code, ttl_seconds)."""
    raw = "".join(secrets.choice(_ALPHABET) for _ in range(8))
    code = f"{raw[:4]}-{raw[4:]}"
    with _lock:
        _pending.clear()
        _pending[_hash(code)] = (str(user_id), time.monotonic() + PAIRING_TTL_SECONDS)
    return code, PAIRING_TTL_SECONDS


def _take(code: str) -> str | None:
    """Spend the code: the user id it was minted for, or None. One shot."""
    with _lock:
        entry = _pending.pop(_hash(code), None)
    if entry is None or entry[1] <= time.monotonic():
        return None
    return entry[0]


def exchange(db: Session, *, code: str, keystone_url: str, publisher_token: str) -> dict:
    """Keystone's half of the handshake. Returns ``{agent_key, contract}``,
    plus ``web_url`` when PantryOS's public address is configured.

    The request is checked BEFORE the code is spent, so a malformed call from
    Keystone does not burn a code the admin is still looking at; the code
    itself is then checked and spent in one step.
    """
    try:
        url = connection.validate_url(keystone_url)
    except ValueError as exc:
        raise PairingRejected(str(exc)) from exc
    token = (publisher_token or "").strip()
    if not token:
        raise PairingRejected("a publisher token is required")

    user_id = _take(code)
    if user_id is None:
        raise PairingRejected("invalid or expired pairing code", bad_code=True)
    user = get_user(db, uuid.UUID(user_id))
    if user is None or not user.is_active:
        raise PairingRejected("invalid or expired pairing code", bad_code=True)

    # Connect first: if that fails (no encryption key, say), nothing has been
    # rotated yet and the current pairing keeps working. Only then mint the
    # new agent key and retire the old one.
    connection.configure(db, keystone_url=url, publisher_token=token)
    previous = get_setting(db, PAIRED_KEY_SETTING)
    key_row, secret = create_agent_key(db, user=user, name=PAIRED_KEY_NAME, scopes="read,write")
    set_setting(db, PAIRED_KEY_SETTING, str(key_row.id))
    if previous:
        try:
            revoke_agent_key(db, uuid.UUID(previous))
        except (LookupError, ValueError):
            pass  # already revoked or deleted by hand: nothing to rotate
    # What the admin screen watches for: a pairing completing (again).
    set_setting(db, PAIRED_AT_SETTING, datetime.now(UTC).isoformat())
    result = {"agent_key": secret, "contract": CONTRACT_VERSION}
    public = web_url.public_web_url()
    if public:
        result["web_url"] = public
    return result
