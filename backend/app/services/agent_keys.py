"""Agent key management — long-lived, revocable credentials for agents.

An agent key is a high-entropy secret, so a plain SHA-256 hash is sufficient (no
password KDF needed). The plaintext is returned once at creation and never again.
"""

from __future__ import annotations

import hashlib
import secrets
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import AgentKey, User

_TOKEN_BYTES = 32
_PREFIX = "pk_"

# Coarse scopes enforced today: "read" (safe methods) and "write" (mutations).
# Finer per-domain scopes can be added later without changing the storage.
KNOWN_SCOPES = {"read", "write"}


def _hash(secret: str) -> str:
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()


def normalize_scopes(scopes: str) -> str:
    """Validate + canonicalize a scopes string; default to read+write."""
    requested = {s.strip().lower() for s in (scopes or "").split(",") if s.strip()}
    unknown = requested - KNOWN_SCOPES
    if unknown:
        raise ValueError(f"Unknown scope(s): {', '.join(sorted(unknown))}. Allowed: {', '.join(sorted(KNOWN_SCOPES))}")
    if not requested:
        requested = {"read", "write"}
    return ",".join(sorted(requested))


def scope_set(scopes: str) -> set[str]:
    return {s.strip().lower() for s in (scopes or "").split(",") if s.strip()}


def create_agent_key(db: Session, *, user: User, name: str, scopes: str = "read,write") -> tuple[AgentKey, str]:
    """Mint a key. Returns the row plus the plaintext secret (shown only now)."""
    secret = _PREFIX + secrets.token_urlsafe(_TOKEN_BYTES)
    row = AgentKey(
        user_id=user.id,
        name=name.strip(),
        token_prefix=secret[: len(_PREFIX) + 4],
        token_hash=_hash(secret),
        scopes=normalize_scopes(scopes),
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row, secret


def list_agent_keys(db: Session) -> list[AgentKey]:
    return list(db.execute(select(AgentKey).order_by(AgentKey.created_at.desc())).scalars().all())


def revoke_agent_key(db: Session, key_id: UUID) -> AgentKey:
    row = db.get(AgentKey, key_id)
    if row is None:
        raise LookupError("Agent key not found")
    row.revoked = True
    db.commit()
    db.refresh(row)
    return row


def authenticate_agent_key(db: Session, secret: str) -> tuple[User, AgentKey] | None:
    """Resolve a presented secret to (owning user, key), or None if invalid/revoked."""
    if not secret:
        return None
    row = db.execute(select(AgentKey).where(AgentKey.token_hash == _hash(secret))).scalar_one_or_none()
    if row is None or row.revoked:
        return None
    user = db.execute(select(User).where(User.id == row.user_id)).scalar_one_or_none()
    if not user or not user.is_active:
        return None
    row.last_used_at = datetime.now(UTC)
    db.commit()
    return user, row
