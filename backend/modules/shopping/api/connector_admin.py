"""Admin Integrations API (ADR 0009) — configure vendor connectors in-app.

Login-gated behind ``require_authenticated_admin`` (never the open kiosk user):
credentials are sensitive. Reads return a redacted view (no secret values);
writes accept partial updates and store them encrypted. ``/test`` validates
credentials against the live service so the admin gets immediate feedback.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.core.crypto import SecretDecryptError, SecretStorageUnavailable
from app.core.security import require_authenticated_admin
from app.db import get_db
from app.services.webhook_deliveries import list_recent_deliveries
from modules.shopping.connectors import NotConfiguredError, get_connector
from modules.shopping.schemas.connector_admin import (
    ConnectorConfigUpdate,
    ConnectorConfigView,
    ConnectorTestResult,
    WebhookDeliveryRead,
)
from modules.shopping.services import connector_config as cfg

router = APIRouter(
    prefix="/admin/integrations",
    tags=["admin-integrations"],
    dependencies=[Depends(require_authenticated_admin)],
)


def _require_configurable(key: str) -> None:
    if key == "classifier" or not cfg.is_configurable(key):
        raise HTTPException(status_code=404, detail=f"Connector '{key}' is not configurable")


@router.get("/kroger/locations")
def kroger_locations_route(
    zip: str = Query(..., min_length=3, max_length=10, description="ZIP to find stores near"),
    db: Session = Depends(get_db),
) -> dict:
    """Find Kroger stores near a ZIP so the admin can pick a location id (ADR 0013)."""
    from app.services import provider_keys
    from modules.catalog.services.barcode_providers import ProviderRateLimited
    from modules.catalog.services.kroger import search_kroger_locations

    client_id, client_secret = provider_keys.kroger_credentials(db)
    if not (client_id and client_secret):
        raise HTTPException(status_code=400, detail="Save the Kroger client ID and secret first.")
    try:
        stores = search_kroger_locations(zip, client_id=client_id, client_secret=client_secret)
    except ProviderRateLimited:
        raise HTTPException(status_code=429, detail="Kroger is rate-limiting; try again shortly.") from None
    return {"stores": stores}


@router.get("/{key}", response_model=ConnectorConfigView)
def get_config_route(key: str, db: Session = Depends(get_db)) -> ConnectorConfigView:
    _require_configurable(key)
    try:
        return ConnectorConfigView(**cfg.get_public(db, key))
    except SecretDecryptError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.put("/{key}", response_model=ConnectorConfigView)
def set_config_route(
    key: str, payload: ConnectorConfigUpdate, db: Session = Depends(get_db)
) -> ConnectorConfigView:
    _require_configurable(key)
    try:
        cfg.set_config(db, key, payload.updates)
    except SecretStorageUnavailable as exc:
        # No CONFIG_SECRET_KEY — can't persist credentials in-app.
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return ConnectorConfigView(**cfg.get_public(db, key))


@router.delete("/{key}", response_model=ConnectorConfigView)
def clear_config_route(key: str, db: Session = Depends(get_db)) -> ConnectorConfigView:
    _require_configurable(key)
    cfg.clear_config(db, key)
    return ConnectorConfigView(**cfg.get_public(db, key))


@router.get("/{key}/deliveries", response_model=list[WebhookDeliveryRead])
def deliveries_route(
    key: str, limit: int = Query(default=10, ge=1, le=100), db: Session = Depends(get_db)
) -> list[WebhookDeliveryRead]:
    """Recent outbound-webhook deliveries (newest first) — "has this ever
    fired?" observability for the Integrations card (ADR 0011). Only the
    home_assistant connector delivers webhooks today."""
    if key != "home_assistant":
        raise HTTPException(status_code=404, detail=f"Connector '{key}' has no deliveries")
    rows = list_recent_deliveries(db, limit=limit)
    return [WebhookDeliveryRead.model_validate(row) for row in rows]


@router.post("/{key}/test", response_model=ConnectorTestResult)
def test_config_route(key: str, db: Session = Depends(get_db)) -> ConnectorTestResult:
    """Validate the effective credentials against the live service (no import)."""
    _require_configurable(key)

    if key == "home_assistant":
        # Not a purchase connector: the test is a signed `pantryos.test` POST to
        # the configured webhook URL (ADR 0011).
        from handlers.webhook_handler import send_test_event

        try:
            send_test_event(cfg.get_effective(db, key))
        except ValueError as exc:
            return ConnectorTestResult(ok=False, detail=str(exc))
        except Exception as exc:  # network/HTTP errors — surface the reason
            return ConnectorTestResult(ok=False, detail=f"{type(exc).__name__}: {exc}")
        return ConnectorTestResult(ok=True, detail="Test event delivered.")

    connector = get_connector(key)
    verify = getattr(connector, "verify", None)
    if connector is None or verify is None:
        raise HTTPException(status_code=400, detail=f"Connector '{key}' has no connection test")

    config = cfg.get_effective(db, key)
    try:
        verify(config)
    except NotConfiguredError as exc:
        return ConnectorTestResult(ok=False, detail=str(exc))
    except Exception as exc:  # imaplib/login/network errors — surface the reason
        return ConnectorTestResult(ok=False, detail=f"{type(exc).__name__}: {exc}")
    return ConnectorTestResult(ok=True, detail="Connected successfully.")
