"""Device pairing + device keys (ADR 0018 §8).

Pairing: an admin mints a one-time code (5-minute TTL, single use, held
in-process — a restart just voids pending codes). The phone exchanges it for
a ``dk_`` key bound to the code's user. Keys are SHA-256 hashed at rest and
authenticate as ``Authorization: Bearer dk_…`` through ``require_user``.
"""

from __future__ import annotations

import hashlib
import secrets
import threading
import time
import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.device_key import DeviceKey
from app.models.user import User

PREFIX = "dk_"
PAIRING_TTL_SECONDS = 300

# code -> (user_id, expires_at_monotonic). In-process by design.
_pending: dict[str, tuple[str, float]] = {}
_lock = threading.Lock()


def _hash(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


def mint_pairing_code(user_id: uuid.UUID) -> tuple[str, int]:
    """A short, phone-typeable one-time code. Returns (code, ttl_seconds)."""
    code = f"{secrets.randbelow(1_000_000):06d}-{secrets.token_hex(3)}"
    with _lock:
        # Purge expired codes opportunistically.
        now = time.monotonic()
        for stale in [c for c, (_, exp) in _pending.items() if exp <= now]:
            _pending.pop(stale, None)
        _pending[code] = (str(user_id), now + PAIRING_TTL_SECONDS)
    return code, PAIRING_TTL_SECONDS


def pair(db: Session, *, code: str, device_name: str) -> tuple[str, DeviceKey] | None:
    """Exchange a pairing code for a new device key. One shot; None if invalid."""
    with _lock:
        entry = _pending.pop(code.strip(), None)
    if entry is None or entry[1] <= time.monotonic():
        return None
    user = db.get(User, uuid.UUID(entry[0]))
    if user is None or not user.is_active:
        return None
    plaintext = PREFIX + secrets.token_urlsafe(32)
    row = DeviceKey(user_id=user.id, key_hash=_hash(plaintext), name=device_name.strip() or "Device")
    db.add(row)
    db.commit()
    return plaintext, row


def authenticate_device_key(db: Session, key: str) -> User | None:
    if not key.startswith(PREFIX):
        return None
    row = db.execute(select(DeviceKey).where(DeviceKey.key_hash == _hash(key))).scalar_one_or_none()
    if row is None or row.revoked_at is not None:
        return None
    user = db.get(User, row.user_id)
    if user is None or not user.is_active:
        return None
    row.last_used_at = datetime.now(timezone.utc)
    db.commit()
    return user


def list_devices(db: Session) -> list[DeviceKey]:
    return list(db.execute(select(DeviceKey).order_by(DeviceKey.created_at.desc())).scalars())


def revoke(db: Session, device_id: uuid.UUID) -> bool:
    row = db.get(DeviceKey, device_id)
    if row is None or row.revoked_at is not None:
        return False
    row.revoked_at = datetime.now(timezone.utc)
    db.commit()
    return True


def revoke_all(db: Session) -> int:
    count = 0
    for row in list_devices(db):
        if row.revoked_at is None:
            row.revoked_at = datetime.now(timezone.utc)
            count += 1
    db.commit()
    return count
