"""Brokered store connections, instance side (ADR 0018 §3).

Owns the ``integration_connections`` rows: the connect handshake (authorize →
claim), the access-token cache (Fernet-encrypted, refreshed from the broker
just before expiry), and disconnect. The broker keeps the refresh tokens;
this module never sees one.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.crypto import decrypt_json, encrypt_json
from app.models.integration_connection import IntegrationConnection
from app.services.integration_broker import (
    BrokerEntitlementLapsed,
    BrokerError,
    BrokerUnavailable,
    get_broker,
)

logger = logging.getLogger(__name__)

_EXPIRY_SKEW_SECONDS = 60
SERVICE_CONNECTION_REF = "service:kroger"
# In-process cache for the shared product-search token: (token, expires_epoch).
_service_token_cache: tuple[str, float] = ("", 0.0)


def get_connection(db: Session, provider: str) -> IntegrationConnection | None:
    return db.execute(
        select(IntegrationConnection).where(IntegrationConnection.provider == provider)
    ).scalar_one_or_none()


def start_connect(db: Session, provider: str, *, redirect_back: str) -> str:
    return get_broker().authorize_url(db, provider, redirect_back=redirect_back)


def complete_claim(db: Session, provider: str, *, claim_code: str) -> IntegrationConnection:
    body = get_broker().claim(db, provider, claim_code=claim_code)
    connection = get_connection(db, provider)
    if connection is None:
        connection = IntegrationConnection(provider=provider, mode="brokered", broker_ref=body["connection_ref"])
        db.add(connection)
    connection.broker_ref = body["connection_ref"]
    connection.scopes = body.get("scopes")
    connection.status = "active"
    connection.last_error = None
    _store_access(connection, body)
    db.commit()
    return connection


def _store_access(connection: IntegrationConnection, body: dict) -> None:
    connection.access_token_ciphertext = encrypt_json({"access_token": body["access_token"]})
    connection.access_expires_at = datetime.fromtimestamp(float(body["expires_at"]), tz=timezone.utc)
    connection.last_used_at = datetime.now(timezone.utc)


def access_token(db: Session, provider: str, *, force: bool = False) -> str:
    """The connection's short-lived access token, from cache or the broker.

    Raises BrokerUnavailable / BrokerEntitlementLapsed / BrokerError for the
    caller to map onto its own error surface.
    """
    connection = get_connection(db, provider)
    if connection is None or connection.status != "active":
        raise BrokerUnavailable(f"No brokered {provider} connection.")

    if not force and connection.access_token_ciphertext and connection.access_expires_at:
        expires = connection.access_expires_at
        if expires.tzinfo is None:
            expires = expires.replace(tzinfo=timezone.utc)
        if expires.timestamp() > time.time() + _EXPIRY_SKEW_SECONDS:
            return decrypt_json(connection.access_token_ciphertext)["access_token"]

    try:
        body = get_broker().access_token(db, connection_ref=connection.broker_ref)
    except (BrokerEntitlementLapsed, BrokerError) as exc:
        connection.last_error = str(exc)
        db.commit()
        raise
    _store_access(connection, body)
    db.commit()
    return decrypt_json(connection.access_token_ciphertext)["access_token"]


def service_search_token(db: Session) -> str | None:
    """Broker-minted product-search token (client_credentials, ADR 0018 §3 D2).

    Quiet by design: returns None on any failure so search providers degrade
    to 'no results from this source' instead of erroring the whole search.
    """
    global _service_token_cache
    token, expires = _service_token_cache
    if token and expires > time.time() + _EXPIRY_SKEW_SECONDS:
        return token
    try:
        body = get_broker().access_token(db, connection_ref=SERVICE_CONNECTION_REF)
    except (BrokerUnavailable, BrokerEntitlementLapsed, BrokerError):
        return None
    _service_token_cache = (body["access_token"], float(body["expires_at"]))
    return _service_token_cache[0]


def reset_service_token_cache() -> None:
    global _service_token_cache
    _service_token_cache = ("", 0.0)


def disconnect(db: Session, provider: str) -> None:
    connection = get_connection(db, provider)
    if connection is None:
        return
    try:
        get_broker().revoke(db, connection_ref=connection.broker_ref)
    except (BrokerUnavailable, BrokerEntitlementLapsed, BrokerError):
        logger.warning("broker revoke failed; removing the local connection anyway")
    db.delete(connection)
    db.commit()
