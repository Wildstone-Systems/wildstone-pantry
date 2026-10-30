"""Effective barcode/product-provider keys: in-app override over env (ADR 0009/0013).

The admin Integrations screen writes encrypted overrides into ``connector_config``
(keys "usda", "kroger"); this platform-level resolver reads the *same* rows so
both the catalog barcode path and the shopping offer search pick up an admin-set
key without a restart, falling back to the environment when unset. Kept in
``app.services`` (not a feature module) so catalog and shopping can both depend
on it cleanly.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.crypto import decrypt_json
from app.models import ConnectorConfig


def _stored(db: Session | None, connector: str) -> dict[str, str]:
    if db is None:
        return {}
    row = db.get(ConnectorConfig, connector)
    if row is None:
        return {}
    try:
        return {str(k): ("" if v is None else str(v)) for k, v in decrypt_json(row.ciphertext).items()}
    except Exception:
        # A deployment that lost/rotated CONFIG_SECRET_KEY: fall back to env.
        return {}


def usda_api_key(db: Session | None) -> str:
    return (_stored(db, "usda").get("api_key") or settings.usda_fdc_api_key or "").strip()


def spoonacular_api_key(db: Session | None) -> str:
    return (_stored(db, "spoonacular").get("api_key") or settings.spoonacular_api_key or "").strip()


def kroger_credentials(db: Session | None) -> tuple[str, str]:
    stored = _stored(db, "kroger")
    client_id = (stored.get("client_id") or settings.kroger_client_id or "").strip()
    client_secret = (stored.get("client_secret") or settings.kroger_client_secret or "").strip()
    return client_id, client_secret


def kroger_location_id(db: Session | None) -> str:
    """Optional Kroger store id (DB-only) — unlocks prices in search results."""
    return (_stored(db, "kroger").get("location_id") or "").strip()


def kroger_redirect_uri(db: Session | None) -> str:
    """Cart-API redirect URI (Phase 4) — in-app override over env, for the
    authorization_code flow. Must match the Kroger dev-portal registration."""
    return (_stored(db, "kroger").get("redirect_uri") or settings.kroger_redirect_uri or "").strip()


def instacart_api_key(db: Session | None) -> str:
    """Instacart Developer Platform API key (ADR 0013 Phase 4b) — admin-configured
    only. Empty = the shoppable-list integration stays dormant."""
    return (_stored(db, "instacart").get("api_key") or "").strip()


def instacart_base_url(db: Session | None) -> str:
    """Optional Instacart base-URL override (e.g. the dev server) — empty means
    the connector uses its production default."""
    return (_stored(db, "instacart").get("base_url") or "").strip()
