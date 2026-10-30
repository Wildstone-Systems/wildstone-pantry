"""Household profile management + event attribution (ADR 0004).

Profiles are persons (no login). Reads are open (the kiosk shows them to pick);
writes are admin-gated at the route layer. ``resolve_actor_profile`` maps a
kiosk-supplied profile id onto the ``actor`` stamped on inventory events.
"""

from __future__ import annotations

import hashlib
import secrets
from decimal import Decimal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import HouseholdProfile, User

_PIN_MIN_LEN = 4
_PIN_MAX_LEN = 12


def _hash_pin(pin: str, salt: str) -> str:
    digest = hashlib.scrypt(pin.encode("utf-8"), salt=salt.encode("utf-8"), n=2**14, r=8, p=1)
    return digest.hex()


def _pin_record(pin: str) -> str:
    salt = secrets.token_hex(16)
    return f"{salt}${_hash_pin(pin, salt)}"


def _verify_pin(pin: str, pin_hash: str) -> bool:
    try:
        salt, digest = pin_hash.split("$", 1)
    except ValueError:
        return False
    return secrets.compare_digest(_hash_pin(pin, salt), digest)


def list_profiles(db: Session, *, include_inactive: bool = False) -> list[HouseholdProfile]:
    stmt = select(HouseholdProfile).order_by(HouseholdProfile.display_name.asc())
    if not include_inactive:
        stmt = stmt.where(HouseholdProfile.is_active.is_(True))
    return list(db.execute(stmt).scalars().all())


def get_profile(db: Session, profile_id: UUID) -> HouseholdProfile | None:
    return db.execute(
        select(HouseholdProfile).where(HouseholdProfile.id == profile_id)
    ).scalar_one_or_none()


def _validate_account(db: Session, account_id: UUID | None) -> None:
    if account_id is None:
        return
    account = db.execute(select(User).where(User.id == account_id)).scalar_one_or_none()
    if account is None:
        raise LookupError("Linked account not found")


def create_profile(
    db: Session,
    *,
    display_name: str,
    account_id: UUID | None = None,
    daily_calorie_target: Decimal | None = None,
) -> HouseholdProfile:
    _validate_account(db, account_id)
    profile = HouseholdProfile(
        display_name=display_name.strip(),
        account_id=account_id,
        daily_calorie_target=daily_calorie_target,
        is_active=True,
    )
    db.add(profile)
    db.commit()
    db.refresh(profile)
    return profile


def update_profile(
    db: Session,
    *,
    profile_id: UUID,
    display_name: str | None = None,
    account_id: UUID | None = None,
    daily_calorie_target: Decimal | None = None,
    is_active: bool | None = None,
    account_id_set: bool = False,
    calorie_target_set: bool = False,
) -> HouseholdProfile:
    """Partial update. ``*_set`` flags distinguish "clear to null" from "leave
    unchanged" for the nullable fields."""
    profile = get_profile(db, profile_id)
    if profile is None:
        raise LookupError("Profile not found")

    if display_name is not None:
        profile.display_name = display_name.strip()
    if account_id_set:
        _validate_account(db, account_id)
        profile.account_id = account_id
    if calorie_target_set:
        profile.daily_calorie_target = daily_calorie_target
    if is_active is not None:
        profile.is_active = is_active

    db.add(profile)
    db.commit()
    db.refresh(profile)
    return profile


def set_profile_pin(db: Session, *, profile_id: UUID, pin: str | None) -> HouseholdProfile:
    """Set or clear a profile's kiosk PIN (admin). ``pin=None``/empty clears it."""
    profile = get_profile(db, profile_id)
    if profile is None:
        raise LookupError("Profile not found")

    if pin is None or pin.strip() == "":
        profile.pin_hash = None
    else:
        cleaned = pin.strip()
        if not (cleaned.isdigit() and _PIN_MIN_LEN <= len(cleaned) <= _PIN_MAX_LEN):
            raise ValueError(f"PIN must be {_PIN_MIN_LEN}-{_PIN_MAX_LEN} digits")
        profile.pin_hash = _pin_record(cleaned)

    db.add(profile)
    db.commit()
    db.refresh(profile)
    return profile


def verify_profile_pin(db: Session, *, profile_id: UUID, pin: str) -> bool:
    """Check a kiosk PIN for a profile (open). A profile with no PIN verifies true
    (nothing to check); a wrong or missing PIN verifies false."""
    profile = get_profile(db, profile_id)
    if profile is None or not profile.is_active:
        return False
    if profile.pin_hash is None:
        return True
    return _verify_pin((pin or "").strip(), profile.pin_hash)


def resolve_actor_profile(db: Session, raw_profile_id: str | None) -> str | None:
    """Best-effort: map a kiosk ``X-Household-Profile`` header to an actor string
    (the profile id). Returns None on a missing/invalid/inactive profile — event
    attribution never fails a write."""
    if not raw_profile_id:
        return None
    try:
        profile_id = UUID(raw_profile_id.strip())
    except (ValueError, AttributeError):
        return None
    profile = get_profile(db, profile_id)
    if profile is None or not profile.is_active:
        return None
    return str(profile.id)
