from __future__ import annotations

from fastapi import Depends, Header, HTTPException, Request
from sqlalchemy.orm import Session

from app.core import settings
from app.db import get_db
from app.services.agent_keys import authenticate_agent_key, scope_set
from app.services.auth import get_or_create_personal_user, get_user_from_token

# HTTP methods that only read; everything else is a mutation requiring "write".
_SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


def auth_enabled_effective(db: Session) -> bool:
    """Whether protected surfaces demand a real bearer token.

    The env var is the base; the first-run wizard's "household accounts"
    choice persists an override in ``app_settings`` (ADR 0018 §7) so the
    decision survives restarts without an env edit. The override can only
    turn auth ON — kiosk endpoints stay open either way (ADR 0004).
    """
    if settings.auth_enabled:
        return True
    from app.services.app_settings import KEY_AUTH_ENABLED_OVERRIDE, is_true

    try:
        return is_true(db, KEY_AUTH_ENABLED_OVERRIDE)
    except Exception:  # noqa: BLE001 - pre-migration DB: fall back to env
        return False


async def require_user(
    request: Request,
    authorization: str | None = Header(default=None),
    x_agent_key: str | None = Header(default=None, alias="X-Agent-Key"),
    db: Session = Depends(get_db),
):
    # Agent keys are a first-class identity (GH #11): they authenticate regardless
    # of the auth_enabled/personal-mode setting and resolve to their owning user.
    # Scopes are enforced by HTTP method — a key without "write" is read-only.
    if x_agent_key:
        resolved = authenticate_agent_key(db, x_agent_key.strip())
        if not resolved:
            raise HTTPException(status_code=401, detail="Invalid or revoked agent key")
        agent_user, key = resolved
        if request.method not in _SAFE_METHODS and "write" not in scope_set(key.scopes):
            raise HTTPException(status_code=403, detail="Agent key lacks the 'write' scope")
        return agent_user

    # Device keys (ADR 0018 §8): a paired phone/tablet presents its dk_ key as
    # a bearer token. Like agent keys, they authenticate regardless of the
    # auth_enabled toggle and resolve to their bound user.
    if authorization and authorization.lower().startswith("bearer dk_"):
        from app.services.device_keys import authenticate_device_key

        device_user = authenticate_device_key(db, authorization.split(" ", 1)[1].strip())
        if not device_user:
            raise HTTPException(status_code=401, detail="Invalid or revoked device key")
        return device_user

    if auth_enabled_effective(db):
        if not authorization or not authorization.lower().startswith("bearer "):
            raise HTTPException(status_code=401, detail="Missing bearer token")

        token = authorization.split(" ", 1)[1].strip()
        user = get_user_from_token(db, token)
        if not user:
            raise HTTPException(status_code=401, detail="Invalid or expired token")

        return user

    # Personal mode: allow requests without tokens. If a valid token is present, prefer it.
    if authorization and authorization.lower().startswith("bearer "):
        token = authorization.split(" ", 1)[1].strip()
        user = get_user_from_token(db, token)
        if user:
            return user

    return get_or_create_personal_user(
        db,
        email=settings.auth_personal_email,
        password=settings.auth_personal_password,
    )


async def allow_kiosk(
    request: Request,
    authorization: str | None = Header(default=None),
    x_agent_key: str | None = Header(default=None, alias="X-Agent-Key"),
    db: Session = Depends(get_db),
):
    """
    Identity for kiosk-open surfaces (ADR 0004).

    The kiosk UI is deliberately not gated behind a login, so these endpoints
    must not demand a bearer token even when ``AUTH_ENABLED`` is on — otherwise
    a wall-mounted tablet cannot add an item without someone typing admin
    credentials. Callers that *do* present a credential are still fully
    validated by ``require_user``: an agent key keeps its scope enforcement
    (ADR 0003), and a bad or expired token is still rejected rather than
    silently downgraded to the kiosk identity. Only a request with no
    credential at all falls back to the personal identity.

    Admin surfaces stay on ``require_admin`` — the kiosk identity is never a
    path in.
    """

    if x_agent_key or authorization:
        return await require_user(request, authorization, x_agent_key, db)

    return get_or_create_personal_user(
        db,
        email=settings.auth_personal_email,
        password=settings.auth_personal_password,
    )


async def deny_agent_keys(x_agent_key: str | None = Header(default=None, alias="X-Agent-Key")):
    """Refuse requests presenting an agent key.

    For the handful of operations that must be a deliberate human act — a
    permanent, cascading delete above all. An agent key is an automation
    credential: it can be handed to a script, left in a config file, or driven
    by a model that mis-reads an instruction, and none of those should be able
    to destroy history. Humans on the kiosk, the web UI or a paired device all
    pass; only the automation identity is turned away.

    This is not the whole guard, only the half that can be enforced by identity.
    The other half is an explicit confirmation the caller has to get exactly
    right — see the purge route.
    """
    if x_agent_key:
        raise HTTPException(
            status_code=403,
            detail="This action must be performed by a person, not an agent key.",
        )


async def require_admin(user=Depends(require_user)):
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="Admin role required")
    return user


async def require_authenticated_admin(
    authorization: str | None = Header(default=None),
    db: Session = Depends(get_db),
):
    """The protected admin surface (ADR 0004): a REAL admin login is always
    required, even in kiosk mode (``AUTH_ENABLED=false``). Unlike ``require_admin``
    this never falls back to the open personal/kiosk user — the login-free kiosk
    identity must not be a path to administration."""
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="Admin login required")

    token = authorization.split(" ", 1)[1].strip()
    user = get_user_from_token(db, token)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid or expired token")
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="Admin role required")
    # The kiosk auto-user (created in personal mode) is role=admin but NEVER
    # provisioned; only a real, bootstrapped admin is. Requiring provisioning here
    # keeps the login-free kiosk identity (and its default password) out of the
    # protected admin surface even when AUTH_ENABLED=false in a protected env.
    if not user.is_provisioned:
        raise HTTPException(status_code=403, detail="Admin account is not provisioned")
    return user


def resolve_actor(
    x_household_profile: str | None = Header(default=None, alias="X-Household-Profile"),
    db: Session = Depends(get_db),
) -> str | None:
    """Kiosk attribution (ADR 0004): map the selected household profile onto the
    ``actor`` recorded on inventory events. Best-effort — a missing or invalid
    profile header simply yields ``None`` and never blocks the operation."""
    from app.services.profiles import resolve_actor_profile

    return resolve_actor_profile(db, x_household_profile)
