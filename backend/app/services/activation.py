"""Instance activation against the hosted license server (ADR 0018 §2).

The instance owns an Ed25519 keypair (private key Fernet-encrypted at rest,
same custody as connector credentials). Activation exchanges an activation
key + the public key for a signed entitlement token; a daily background
refresh renews it; deactivation frees the seat. Token *verification* is pure
local (``verify_token``) against the public keys embedded via
``LICENSE_PUBLIC_KEYS`` — resolve-time code never touches the network.
"""

from __future__ import annotations

import base64
import logging
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import httpx
import jwt
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core import settings
from app.core.crypto import decrypt_json, encrypt_json, secrets_available
from app.models.entitlement_state import EntitlementState

logger = logging.getLogger(__name__)

# Lifecycle numbers (ADR 0018 §2).
GRACE = timedelta(days=14)
REFRESH_EVERY = timedelta(hours=24)


class ActivationError(RuntimeError):
    pass


def _b64e(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _b64d(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def get_state(db: Session) -> EntitlementState | None:
    return db.execute(select(EntitlementState)).scalars().first()


def _public_keys() -> list[Ed25519PublicKey]:
    keys = []
    for part in settings.license_public_keys.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            keys.append(Ed25519PublicKey.from_public_bytes(_b64d(part)))
        except Exception:  # noqa: BLE001 - a malformed key is skipped, loudly
            logger.warning("LICENSE_PUBLIC_KEYS entry is not a valid Ed25519 key; skipping")
    return keys


@dataclass(frozen=True)
class VerifiedToken:
    tier: str
    features: tuple[str, ...]
    expires_at: datetime
    in_grace: bool


def verify_token(token: str, *, now: datetime | None = None) -> VerifiedToken | None:
    """Offline verification with the ADR 0018 grace + clock rules.

    Returns None when the token fails signature checks or is past
    expiry + grace. A wound-back system clock cannot extend life: time is
    measured from max(system_time, iat).
    """
    now = now or datetime.now(timezone.utc)
    for key in _public_keys():
        try:
            claims = jwt.decode(token, key, algorithms=["EdDSA"], options={"verify_exp": False})
        except jwt.InvalidTokenError:
            continue
        issued = datetime.fromtimestamp(int(claims.get("iat", 0)), tz=timezone.utc)
        expires = datetime.fromtimestamp(int(claims.get("exp", 0)), tz=timezone.utc)
        effective_now = max(now, issued)
        if effective_now > expires + GRACE:
            return None
        return VerifiedToken(
            tier=str(claims.get("tier", "premium")),
            features=tuple(claims.get("features", [])),
            expires_at=expires,
            in_grace=effective_now > expires,
        )
    return None


def activate(db: Session, *, key: str, instance_name: str = "PantryOS") -> EntitlementState:
    server = settings.license_server_url.strip().rstrip("/")
    if not server:
        raise ActivationError("LICENSE_SERVER_URL is not configured — this instance is in free mode.")
    if not secrets_available():
        raise ActivationError("CONFIG_SECRET_KEY must be set before activating (it protects the instance key).")
    if not _public_keys():
        raise ActivationError("LICENSE_PUBLIC_KEYS is not configured — cannot verify entitlement tokens.")

    existing = get_state(db)
    if existing is not None:
        # Reuse the existing keypair so re-activation keeps the same seat.
        private = Ed25519PrivateKey.from_private_bytes(
            _b64d(decrypt_json(existing.private_key_ciphertext)["seed"])
        )
    else:
        private = Ed25519PrivateKey.generate()

    pubkey_b64 = _b64e(private.public_key().public_bytes_raw())
    try:
        resp = httpx.post(
            f"{server}/v1/activate",
            json={"key": key.strip(), "instance_pubkey": pubkey_b64, "instance_name": instance_name},
            timeout=10,
        )
    except httpx.HTTPError as exc:
        raise ActivationError(f"Could not reach the license server: {exc}") from exc
    if resp.status_code == 401:
        raise ActivationError("Invalid activation key.")
    if resp.status_code == 409:
        raise ActivationError(resp.json().get("detail", "This key has no free instance seats."))
    if resp.status_code != 200:
        raise ActivationError(f"License server error ({resp.status_code}).")
    body = resp.json()

    verified = verify_token(body["token"])
    if verified is None:
        raise ActivationError(
            "The license server's token did not verify against LICENSE_PUBLIC_KEYS — "
            "server and app key material are out of sync."
        )

    if existing is None:
        existing = EntitlementState(
            instance_id=body["instance_id"],
            key_hint=key.strip()[-4:],
            tier=body["tier"],
            token=body["token"],
            private_key_ciphertext=encrypt_json({"seed": _b64e(private.private_bytes_raw())}),
            server_url=server,
            expires_at=verified.expires_at,
        )
        db.add(existing)
    else:
        existing.instance_id = body["instance_id"]
        existing.key_hint = key.strip()[-4:]
        existing.tier = body["tier"]
        existing.token = body["token"]
        existing.server_url = server
        existing.expires_at = verified.expires_at
        existing.refresh_failures = 0
    existing.last_refresh_at = datetime.now(timezone.utc)
    db.commit()
    bust_entitlement_cache()
    return existing


def _signed_payload(state: EntitlementState) -> dict:
    private = Ed25519PrivateKey.from_private_bytes(_b64d(decrypt_json(state.private_key_ciphertext)["seed"]))
    timestamp = int(time.time())
    message = f"{state.instance_id}.{timestamp}".encode()
    return {
        "instance_id": state.instance_id,
        "timestamp": timestamp,
        "signature": _b64e(private.sign(message)),
    }


def refresh(db: Session) -> bool:
    """Renew the entitlement token. Returns True on success; failures only
    bump a counter — expiry + grace handles the rest (fail-safe, never brick)."""
    state = get_state(db)
    if state is None:
        return False
    try:
        resp = httpx.post(f"{state.server_url}/v1/refresh", json=_signed_payload(state), timeout=10)
        if resp.status_code == 200:
            body = resp.json()
            verified = verify_token(body["token"])
            if verified is None:
                raise ActivationError("refreshed token failed local verification")
            state.token = body["token"]
            state.tier = body["tier"]
            state.expires_at = verified.expires_at
            state.refresh_failures = 0
            state.last_refresh_at = datetime.now(timezone.utc)
            db.commit()
            bust_entitlement_cache()
            return True
        if resp.status_code == 402:
            logger.warning("entitlement refresh refused: subscription not active (key …%s)", state.key_hint)
    except (httpx.HTTPError, ActivationError) as exc:
        logger.info("entitlement refresh failed (will retry): %s", exc)
    state.refresh_failures += 1
    db.commit()
    return False


def refresh_due(state: EntitlementState, *, now: datetime | None = None) -> bool:
    now = now or datetime.now(timezone.utc)
    last = state.last_refresh_at
    if last is not None and last.tzinfo is None:
        last = last.replace(tzinfo=timezone.utc)
    return last is None or (now - last) >= REFRESH_EVERY


def deactivate(db: Session) -> None:
    state = get_state(db)
    if state is None:
        return
    try:
        httpx.post(f"{state.server_url}/v1/deactivate", json=_signed_payload(state), timeout=10)
    except httpx.HTTPError:
        logger.warning("deactivate call failed; freeing the seat locally anyway")
    db.delete(state)
    db.commit()
    bust_entitlement_cache()


def bust_entitlement_cache() -> None:
    from app.services import entitlement

    entitlement.bust_cache()
