"""Native OIDC SSO routes (ADR 0020 Chunk C).

Three public routes (no auth dependency — this IS the login path). The
callback is a browser navigation, not an XHR: every outcome — success,
IdP-reported error, rate limit, or a verification/mapping failure — is a 302
redirect back into the SPA, with the result carried in the URL fragment
(never the query string, so it never lands in server access logs).
"""

from __future__ import annotations

from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.core.ratelimit import login_limiter
from app.db import get_db
from app.services import app_settings, oidc

router = APIRouter(prefix="/auth/oidc", tags=["auth"])


# Redirect target for all callback outcomes: /admin is where AdminGuard mounts
# LoginPage for an unauthenticated browser (there is no standalone /login route
# in the SPA — the wildcard would swallow it and drop the fragment).
_SPA_LOGIN_PATH = "/admin"


def _sso_error_redirect(message: str) -> RedirectResponse:
    return RedirectResponse(url=f"{_SPA_LOGIN_PATH}#sso_error={quote(message)}", status_code=302)


@router.get("/status")
def status_route(db: Session = Depends(get_db)) -> dict:
    """Never errors: unconfigured OIDC or a pre-migration DB both degrade to
    safe defaults rather than a 500 on the login page's probe."""
    try:
        enabled = oidc.enabled(db)
        cfg = oidc.effective_config(db)
        button_label = cfg.get("button_label") or oidc.DEFAULTS["button_label"]
    except Exception:  # noqa: BLE001 - status must never error
        enabled = False
        button_label = oidc.DEFAULTS["button_label"]

    try:
        local_login_hidden = app_settings.is_true(db, app_settings.KEY_LOCAL_LOGIN_HIDDEN)
    except Exception:  # noqa: BLE001 - pre-migration DB: mirror auth_enabled_effective
        local_login_hidden = False

    return {
        "enabled": enabled,
        "button_label": button_label,
        "local_login_hidden": local_login_hidden,
    }


@router.get("/login")
def login_route(request: Request, db: Session = Depends(get_db)):
    if not oidc.enabled(db):
        raise HTTPException(status_code=404, detail="SSO is not configured.")

    try:
        url = oidc.begin_login(db, request)
    except oidc.OidcError as exc:
        return _sso_error_redirect(str(exc))

    return RedirectResponse(url=url, status_code=302)


@router.get("/callback")
def callback_route(
    request: Request,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
    error_description: str | None = None,
    db: Session = Depends(get_db),
):
    if error:
        return _sso_error_redirect(error_description or error)

    client_host = request.client.host if request.client else "unknown"
    limiter_key = f"oidc:{client_host}"
    try:
        login_limiter.check(limiter_key)
    except HTTPException as exc:
        return _sso_error_redirect(str(exc.detail))

    if not code or not state:
        login_limiter.record_failure(limiter_key)
        return _sso_error_redirect("The identity provider did not return a login code.")

    try:
        _user, token = oidc.complete_login(db, request, code, state)
    except oidc.OidcError as exc:
        login_limiter.record_failure(limiter_key)
        return _sso_error_redirect(str(exc))

    login_limiter.record_success(limiter_key)
    return RedirectResponse(url=f"{_SPA_LOGIN_PATH}#sso_token={quote(token.token)}", status_code=302)
