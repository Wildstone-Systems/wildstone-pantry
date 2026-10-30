"""Kroger Cart OAuth (Phase 4, ADR 0013).

Three-legged authorization_code flow so a household member can authorize the
Kroger cart once. ``/oauth/start`` and status/disconnect are admin-gated; the
``/oauth/callback`` is deliberately PUBLIC because Kroger redirects the user's
browser to it (no Authorization header rides along) — it's protected by the
one-shot ``state`` we issued, then bounces back to the SPA integrations page.
"""

from __future__ import annotations

import secrets

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.core.security import require_authenticated_admin
from app.db import get_db
from app.services import provider_keys
from app.services.entitlement import FEATURE_STORE_INTEGRATIONS, check_brokered_feature, require_feature
from modules.shopping.services import kroger_cart as kc

router = APIRouter(prefix="/integrations/kroger", tags=["kroger-oauth"])


@router.get("/oauth/status", dependencies=[Depends(require_authenticated_admin)])
def status_route(db: Session = Depends(get_db)) -> dict:
    """Is a Kroger account connected, and is the flow even configurable?"""
    status = kc.connection_status(db)
    status["redirect_uri"] = provider_keys.kroger_redirect_uri(db)
    status["has_credentials"] = all(provider_keys.kroger_credentials(db))
    return status


@router.post("/oauth/start", dependencies=[Depends(require_authenticated_admin)])
def start_route(db: Session = Depends(get_db)) -> dict:
    """Mint a state and return the Kroger consent URL for the SPA to navigate to."""
    client_id, _secret = provider_keys.kroger_credentials(db)
    redirect_uri = provider_keys.kroger_redirect_uri(db)
    if not client_id:
        # No BYO credentials: the only remaining path is the brokered one,
        # which is the premium feature (ADR 0018 §3). 402 when a broker is
        # configured but unentitled; otherwise the usual config error.
        check_brokered_feature(FEATURE_STORE_INTEGRATIONS)
        raise HTTPException(status_code=400, detail="Set the Kroger client ID and secret first.")
    if not redirect_uri:
        raise HTTPException(status_code=400, detail="Set the Kroger redirect URI first.")
    state = secrets.token_urlsafe(24)
    kc.save_oauth_state(db, state)
    return {"authorize_url": kc.build_authorize_url(client_id=client_id, redirect_uri=redirect_uri, state=state)}


@router.get("/oauth/callback")
def callback_route(
    code: str = Query(default=""),
    state: str = Query(default=""),
    error: str = Query(default=""),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    """Kroger redirects the browser here with ?code&state. Exchange, store, and
    bounce back to the SPA integrations page with a status flag."""
    redirect_uri = provider_keys.kroger_redirect_uri(db)
    origin = kc.app_origin(redirect_uri)
    dest = f"{origin}/admin/integrations" if origin else "/admin/integrations"

    if error or not code or not kc.verify_and_consume_state(db, state):
        return RedirectResponse(f"{dest}?kroger=error", status_code=302)

    client_id, client_secret = provider_keys.kroger_credentials(db)
    try:
        kc.exchange_code(
            db, code=code, client_id=client_id, client_secret=client_secret, redirect_uri=redirect_uri
        )
    except Exception:
        return RedirectResponse(f"{dest}?kroger=error", status_code=302)
    return RedirectResponse(f"{dest}?kroger=connected", status_code=302)


@router.post("/oauth/disconnect", dependencies=[Depends(require_authenticated_admin)])
def disconnect_route(db: Session = Depends(get_db)) -> dict:
    kc.disconnect(db)
    return {"connected": False}


# ---- Brokered connection (PantryOS Connect, ADR 0018 §3) ---------------------
# No credentials, no redirect-URI registration: the hosted broker owns the
# OAuth app. Start is admin + entitlement gated; the callback is public by
# necessity (the browser arrives from the broker carrying a one-time claim
# code, which the instance exchanges over a signed channel).


@router.post(
    "/broker/start",
    dependencies=[Depends(require_authenticated_admin), Depends(require_feature(FEATURE_STORE_INTEGRATIONS))],
)
def broker_start_route(request: Request, db: Session = Depends(get_db)) -> dict:
    from app.services import broker_connections
    from app.services.integration_broker import BrokerEntitlementLapsed, BrokerError, BrokerUnavailable

    redirect_back = str(request.base_url).rstrip("/") + "/api/integrations/kroger/broker/callback"
    try:
        return {"authorize_url": broker_connections.start_connect(db, "kroger", redirect_back=redirect_back)}
    except BrokerUnavailable as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except BrokerEntitlementLapsed as exc:
        raise HTTPException(status_code=402, detail=str(exc)) from exc
    except BrokerError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.get("/broker/callback")
def broker_callback_route(
    kroger_claim: str = Query(default=""),
    kroger: str = Query(default=""),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    from app.services import broker_connections
    from app.services.integration_broker import BrokerEntitlementLapsed, BrokerError, BrokerUnavailable

    dest = "/admin/integrations"
    if kroger == "error" or not kroger_claim:
        return RedirectResponse(f"{dest}?kroger=error", status_code=302)
    try:
        broker_connections.complete_claim(db, "kroger", claim_code=kroger_claim)
    except (BrokerUnavailable, BrokerEntitlementLapsed, BrokerError):
        return RedirectResponse(f"{dest}?kroger=error", status_code=302)
    return RedirectResponse(f"{dest}?kroger=connected", status_code=302)


@router.get("/broker/status", dependencies=[Depends(require_authenticated_admin)])
def broker_status_route(db: Session = Depends(get_db)) -> dict:
    from app.services import broker_connections
    from app.services.integration_broker import get_broker

    connection = broker_connections.get_connection(db, "kroger")
    return {
        "broker_available": get_broker().is_available(),
        "connected": connection is not None and connection.status == "active",
        "last_error": connection.last_error if connection else None,
    }


@router.post("/broker/disconnect", dependencies=[Depends(require_authenticated_admin)])
def broker_disconnect_route(db: Session = Depends(get_db)) -> dict:
    from app.services import broker_connections

    broker_connections.disconnect(db, "kroger")
    return {"connected": False}
