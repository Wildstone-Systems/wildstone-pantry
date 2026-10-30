"""Symmetric encryption for secrets stored in the DB (ADR 0009).

Connector credentials entered in the admin Integrations screen are encrypted at
rest with Fernet (AES-128-CBC + HMAC) under ``settings.config_secret_key``. When
no key is configured, secret storage is disabled — callers get a clear error
rather than silently persisting plaintext.
"""

from __future__ import annotations

import json
from typing import Any

from cryptography.fernet import Fernet, InvalidToken

from app.core.config import settings


class SecretStorageUnavailable(RuntimeError):
    """Raised when secret encryption is requested but no CONFIG_SECRET_KEY is set."""


class SecretDecryptError(RuntimeError):
    """Raised when a stored ciphertext can't be decrypted (wrong/rotated key)."""


def secrets_available() -> bool:
    """True when a usable encryption key is configured."""
    return bool(settings.config_secret_key.strip())


def _fernet() -> Fernet:
    key = settings.config_secret_key.strip()
    if not key:
        raise SecretStorageUnavailable(
            "CONFIG_SECRET_KEY is not set — cannot store connector credentials in-app. "
            "Set it in the environment (or use env-only config)."
        )
    try:
        return Fernet(key.encode())
    except (ValueError, TypeError) as exc:
        raise SecretStorageUnavailable(f"CONFIG_SECRET_KEY is not a valid Fernet key: {exc}") from exc


def encrypt_json(payload: dict[str, Any]) -> str:
    """Encrypt a JSON-serializable dict to a Fernet token (str)."""
    token = _fernet().encrypt(json.dumps(payload).encode("utf-8"))
    return token.decode("ascii")


def decrypt_json(token: str) -> dict[str, Any]:
    """Decrypt a Fernet token back into a dict."""
    try:
        raw = _fernet().decrypt(token.encode("ascii"))
    except InvalidToken as exc:
        raise SecretDecryptError("Stored credentials could not be decrypted (key changed?).") from exc
    return json.loads(raw.decode("utf-8"))
