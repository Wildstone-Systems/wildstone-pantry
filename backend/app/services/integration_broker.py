"""OAuth brokering for third-party store integrations (ADR 0018 §3).

Third-party providers (Kroger's Cart API, etc.) require a pre-registered
redirect URI and a client secret. A distributable self-hosted app can't ship a
secret, and no single hostname-based redirect URI works across every install.

The hosted broker registers ONE OAuth app per provider (its URL is the single
redirect target), holds the client secret AND the refresh tokens, and mints
short-lived access tokens on demand — hybrid custody. Entitlement enforcement
is structural: a lapsed subscription can't mint tokens.

This module is the CLIENT side: free mode gets a ``NullBroker`` (brokered
integrations simply unavailable), connected mode gets an ``HttpBroker``
pointed at ``INTEGRATION_BROKER_URL``. Every broker request is signed with
the instance's Ed25519 key from the activation record (ADR 0018 §2) — an
unactivated instance has no broker identity.
"""

from __future__ import annotations

import base64
import time
from typing import Protocol, runtime_checkable

import httpx
from sqlalchemy.orm import Session

from app.core import settings


class BrokerUnavailable(RuntimeError):
    """A brokered integration was used without a configured broker/activation."""


class BrokerEntitlementLapsed(RuntimeError):
    """The broker refused (402): the subscription behind this instance lapsed."""


class BrokerError(RuntimeError):
    """Any other broker-side failure, with a human-readable reason."""


@runtime_checkable
class IntegrationBroker(Protocol):
    def is_available(self) -> bool: ...

    def authorize_url(self, db: Session, provider: str, *, redirect_back: str) -> str:
        """Provider consent URL; the broker is the OAuth redirect target."""
        ...

    def claim(self, db: Session, provider: str, *, claim_code: str) -> dict:
        """Trade the one-time claim code from the OAuth relay for
        {connection_ref, access_token, expires_at, scopes}."""
        ...

    def access_token(self, db: Session, *, connection_ref: str) -> dict:
        """Mint a short-lived access token: {access_token, expires_at}."""
        ...

    def revoke(self, db: Session, *, connection_ref: str) -> None: ...


_FREE_MODE_MESSAGE = (
    "store integrations via the broker require PantryOS Connect "
    "(no INTEGRATION_BROKER_URL configured)."
)


class NullBroker:
    """Free mode: brokered integrations are unavailable, not broken."""

    def is_available(self) -> bool:
        return False

    def authorize_url(self, db: Session, provider: str, *, redirect_back: str) -> str:
        raise BrokerUnavailable(f"'{provider}' {_FREE_MODE_MESSAGE}")

    def claim(self, db: Session, provider: str, *, claim_code: str) -> dict:
        raise BrokerUnavailable(f"'{provider}' {_FREE_MODE_MESSAGE}")

    def access_token(self, db: Session, *, connection_ref: str) -> dict:
        raise BrokerUnavailable(f"'{connection_ref}' {_FREE_MODE_MESSAGE}")

    def revoke(self, db: Session, *, connection_ref: str) -> None:
        raise BrokerUnavailable(f"'{connection_ref}' {_FREE_MODE_MESSAGE}")


class HttpBroker:
    """Connected mode: the hosted broker, called with instance-signed requests.

    The signature covers ``instance_id.timestamp.extra`` where ``extra`` is the
    endpoint's primary parameter, so a captured request can't be replayed
    against a different endpoint or target.
    """

    def __init__(self, base_url: str) -> None:
        self._base_url = base_url.rstrip("/")

    @property
    def base_url(self) -> str:
        return self._base_url

    def is_available(self) -> bool:
        return True

    def _signed_payload(self, db: Session, extra: str) -> dict:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

        from app.core.crypto import decrypt_json
        from app.services.activation import get_state

        state = get_state(db)
        if state is None:
            raise BrokerUnavailable("This instance is not activated — enter an activation key first.")
        seed = decrypt_json(state.private_key_ciphertext)["seed"]
        private = Ed25519PrivateKey.from_private_bytes(
            base64.urlsafe_b64decode(seed + "=" * (-len(seed) % 4))
        )
        timestamp = int(time.time())
        message = f"{state.instance_id}.{timestamp}.{extra}".encode()
        signature = base64.urlsafe_b64encode(private.sign(message)).decode().rstrip("=")
        return {
            "instance_id": state.instance_id,
            "timestamp": timestamp,
            "signature": signature,
            "extra": extra,
        }

    def _post(self, db: Session, path: str, *, extra: str) -> dict:
        try:
            resp = httpx.post(f"{self._base_url}{path}", json=self._signed_payload(db, extra), timeout=10)
        except httpx.HTTPError as exc:
            raise BrokerError(f"Could not reach the integration broker: {exc}") from exc
        if resp.status_code == 402:
            raise BrokerEntitlementLapsed("The subscription behind this instance is not active.")
        if resp.status_code >= 400:
            detail = ""
            try:
                detail = resp.json().get("detail", "")
            except Exception:  # noqa: BLE001
                detail = resp.text[:200]
            raise BrokerError(f"Broker error ({resp.status_code}): {detail}")
        return resp.json()

    def authorize_url(self, db: Session, provider: str, *, redirect_back: str) -> str:
        body = self._post(db, f"/v1/oauth/{provider}/authorize-url", extra=redirect_back)
        return body["authorize_url"]

    def claim(self, db: Session, provider: str, *, claim_code: str) -> dict:
        return self._post(db, f"/v1/oauth/{provider}/claim", extra=claim_code)

    def access_token(self, db: Session, *, connection_ref: str) -> dict:
        return self._post(db, "/v1/token", extra=connection_ref)

    def revoke(self, db: Session, *, connection_ref: str) -> None:
        self._post(db, "/v1/connections/revoke", extra=connection_ref)


def get_broker() -> IntegrationBroker:
    """The broker for this instance's mode: HttpBroker if configured, else NullBroker."""
    url = settings.integration_broker_url.strip()
    return HttpBroker(url) if url else NullBroker()
