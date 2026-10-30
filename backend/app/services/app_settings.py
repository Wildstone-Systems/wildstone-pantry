"""Instance-level key/value settings (ADR 0018 §7).

Plain strings only; callers own their encoding. Well-known keys live here as
constants so the wizard, security layer, and future admin settings screens
agree on names.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.app_setting import AppSetting

KEY_SETUP_COMPLETED = "setup_completed"
KEY_HOUSEHOLD_NAME = "household_name"
KEY_AUTH_ENABLED_OVERRIDE = "auth_enabled_override"
KEY_LAN_OPEN_ACK = "lan_open_ack"
KEY_LOCAL_LOGIN_HIDDEN = "auth.local_login_hidden"

TRUE = "true"


def get_setting(db: Session, key: str) -> str | None:
    row = db.execute(select(AppSetting).where(AppSetting.key == key)).scalar_one_or_none()
    return row.value if row else None


def set_setting(db: Session, key: str, value: str) -> None:
    row = db.execute(select(AppSetting).where(AppSetting.key == key)).scalar_one_or_none()
    if row:
        row.value = value
    else:
        db.add(AppSetting(key=key, value=value))
    db.commit()


def is_true(db: Session, key: str) -> bool:
    return (get_setting(db, key) or "").strip().lower() == TRUE
