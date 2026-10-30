from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core import settings
from app.models import AuthToken, User


def _hash_password(password: str, salt: str) -> str:
    digest = hashlib.scrypt(password.encode("utf-8"), salt=salt.encode("utf-8"), n=2**14, r=8, p=1)
    return digest.hex()


def _password_record(password: str) -> str:
    salt = secrets.token_hex(16)
    return f"{salt}${_hash_password(password, salt)}"


def _verify_password(password: str, password_hash: str) -> bool:
    try:
        salt, digest = password_hash.split("$", 1)
    except ValueError:
        return False
    return secrets.compare_digest(_hash_password(password, salt), digest)


def needs_admin_setup(db: Session) -> bool:
    """True when no human-provisioned admin exists yet (first-run). The kiosk
    personal-mode auto-user does not count — it is unprovisioned."""
    provisioned = db.execute(
        select(User).where(User.is_provisioned.is_(True)).limit(1)
    ).scalar_one_or_none()
    return provisioned is None


def bootstrap_admin(db: Session, *, email: str, password: str) -> User:
    """Set up the first real admin. Allowed until a provisioned admin exists —
    so it still works in kiosk mode where an unprovisioned personal-mode user was
    auto-created. Claims an existing unprovisioned user (same email, else the
    lone auto-user) rather than leaving a stray account behind."""
    if not needs_admin_setup(db):
        raise ValueError("Admin already configured")

    email_n = email.lower().strip()

    # Prefer claiming a user with the same email; otherwise claim the single
    # existing (unprovisioned) auto-user if there is exactly one.
    claim = db.execute(select(User).where(User.email == email_n)).scalar_one_or_none()
    if claim is None:
        rows = list(db.execute(select(User)).scalars().all())
        claim = rows[0] if len(rows) == 1 else None

    if claim is not None:
        claim.email = email_n
        claim.password_hash = _password_record(password)
        claim.role = "admin"
        claim.is_active = True
        claim.is_provisioned = True
        user = claim
    else:
        user = User(
            email=email_n,
            password_hash=_password_record(password),
            role="admin",
            is_active=True,
            is_provisioned=True,
        )
        db.add(user)

    db.commit()
    db.refresh(user)
    return user


def mint_token(db: Session, user: User) -> AuthToken:
    """Mint an ``AuthToken`` for ``user`` using the standard TTL. Shared by
    password login (``authenticate``) and SSO login (``oidc.complete_login``,
    ADR 0020) so both paths mint sessions identically."""
    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(hours=settings.auth_token_ttl_hours)
    token_value = secrets.token_urlsafe(48)

    token = AuthToken(
        user_id=user.id,
        token=token_value,
        created_at=now,
        expires_at=expires_at,
    )
    db.add(token)
    db.commit()
    db.refresh(token)
    return token


def authenticate(db: Session, *, email: str, password: str) -> tuple[User, AuthToken]:
    user = db.execute(select(User).where(User.email == email.lower().strip())).scalar_one_or_none()
    if not user or not user.is_active:
        raise LookupError("Invalid credentials")
    if not _verify_password(password, user.password_hash):
        raise LookupError("Invalid credentials")

    return user, mint_token(db, user)


def get_user_from_token(db: Session, token: str) -> User | None:
    if not token:
        return None

    auth_token = db.execute(select(AuthToken).where(AuthToken.token == token)).scalar_one_or_none()
    if not auth_token:
        return None

    now = datetime.now(timezone.utc)
    expires = auth_token.expires_at
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=timezone.utc)
    if expires < now:
        return None

    user = db.execute(select(User).where(User.id == auth_token.user_id)).scalar_one_or_none()
    if not user or not user.is_active:
        return None
    return user


def create_user(db: Session, *, email: str, password: str, role: str = "member") -> User:
    """Minimal user management (ADR 0018 §8): the admin creates named accounts
    for family members. Raises ValueError on duplicates/invalid input."""
    email = email.strip().lower()
    if not email or "@" not in email:
        raise ValueError("A valid email is required.")
    if len(password) < 8:
        raise ValueError("Password must be at least 8 characters.")
    if role not in {"member", "admin"}:
        raise ValueError("Role must be 'member' or 'admin'.")
    existing = db.execute(select(User).where(User.email == email)).scalar_one_or_none()
    if existing is not None:
        raise ValueError("A user with that email already exists.")
    user = User(email=email, password_hash=_password_record(password), role=role, is_active=True, is_provisioned=True)
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def set_user_password(db: Session, *, user_id: UUID, password: str) -> bool:
    if len(password) < 8:
        raise ValueError("Password must be at least 8 characters.")
    user = db.get(User, user_id)
    if user is None:
        return False
    user.password_hash = _password_record(password)
    db.commit()
    return True


def list_users(db: Session) -> list[User]:
    return list(db.execute(select(User).order_by(User.created_at.asc())).scalars().all())


def get_user(db: Session, user_id: UUID) -> User | None:
    return db.execute(select(User).where(User.id == user_id)).scalar_one_or_none()


def _promote_personal_user(db: Session, user: User) -> User:
    changed = False
    if not user.is_active:
        user.is_active = True
        changed = True
    if user.role != "admin":
        user.role = "admin"
        changed = True

    if changed:
        db.add(user)
        db.commit()
        db.refresh(user)
    return user


def _first_active_user(db: Session, *, role: str | None = None) -> User | None:
    q = select(User).where(User.is_active.is_(True)).order_by(User.created_at.asc()).limit(1)
    if role is not None:
        q = q.where(User.role == role)
    return db.execute(q).scalar_one_or_none()


def get_or_create_personal_user(db: Session, *, email: str, password: str) -> User:
    """
    Personal-mode helper.

    When AUTH is disabled, the API still needs a stable user context for auditing
    and per-user tables (like feedback). We prefer an existing admin user if one
    exists, otherwise we create a local admin user.
    """

    normalized_email = (email or "").lower().strip()

    if normalized_email:
        existing = db.execute(select(User).where(User.email == normalized_email)).scalar_one_or_none()
        if existing:
            return _promote_personal_user(db, existing)

    existing_admin = _first_active_user(db, role="admin")
    if existing_admin:
        return _promote_personal_user(db, existing_admin)

    existing_user = _first_active_user(db)
    if existing_user:
        return _promote_personal_user(db, existing_user)

    password_value = (password or "").strip() or secrets.token_urlsafe(32)
    user = User(
        email=normalized_email or "admin@pantryos.local",
        password_hash=_password_record(password_value),
        role="admin",
        is_active=True,
    )
    db.add(user)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        if normalized_email:
            existing = db.execute(select(User).where(User.email == normalized_email)).scalar_one_or_none()
            if existing:
                return _promote_personal_user(db, existing)
        existing_user = _first_active_user(db, role="admin") or _first_active_user(db)
        if existing_user:
            return _promote_personal_user(db, existing_user)
        raise
    db.refresh(user)
    return user
