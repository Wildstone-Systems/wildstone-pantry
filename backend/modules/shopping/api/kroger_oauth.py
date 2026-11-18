"""Kroger Cart OAuth (Phase 4, ADR 0013).

Three-legged authorization_code flow so a household member can authorize the
Kroger cart once. ``/oauth/start`` and status/disconnect are admin-gated; the
``/oauth/callback`` is deliberately PUBLIC because Kroger redirects the user's
browser to it (no Authorization header rides along) — it's protected by the
one-shot ``state`` we issued, then bounces back to the SPA integrations page.
"""

from __future__ import annotations

import secrets

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.core.security import require_authenticated_admin
from app.db import get_db
from app.services import provider_keys
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
