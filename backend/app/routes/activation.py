"""Activation key management (ADR 0018 §2). Real-admin-only.

Enter a key → the instance registers with the license server and stores the
signed entitlement. Deactivation frees the seat. Attempts share the auth
failure limiter so the endpoint can't be brute-forced.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.ratelimit import login_limiter
from app.core.security import require_authenticated_admin
from app.db import get_db
from app.services import activation as activation_service

router = APIRouter(prefix="/activation", tags=["activation"])


class ActivateRequest(BaseModel):
    key: str = Field(min_length=8, max_length=64)
    instance_name: str = Field(default="PantryOS", max_length=120)


class ActivationStatus(BaseModel):
    active: bool
    key_hint: str | None = None
    tier: str | None = None
    expires_at: str | None = None
    last_refresh_at: str | None = None
    refresh_failures: int | None = None


@router.get("", response_model=ActivationStatus, dependencies=[Depends(require_authenticated_admin)])
def status_route(db: Session = Depends(get_db)) -> ActivationStatus:
    state = activation_service.get_state(db)
    if state is None:
        return ActivationStatus(active=False)
    return ActivationStatus(
        active=True,
        key_hint=state.key_hint,
        tier=state.tier,
        expires_at=state.expires_at.isoformat() if state.expires_at else None,
        last_refresh_at=state.last_refresh_at.isoformat() if state.last_refresh_at else None,
        refresh_failures=state.refresh_failures,
    )


@router.post("", response_model=ActivationStatus, dependencies=[Depends(require_authenticated_admin)])
def activate_route(payload: ActivateRequest, request: Request, db: Session = Depends(get_db)) -> ActivationStatus:
    client_host = request.client.host if request.client else "unknown"
    limiter_key = f"activation:{client_host}"
    login_limiter.check(limiter_key)
    try:
        activation_service.activate(db, key=payload.key, instance_name=payload.instance_name)
    except activation_service.ActivationError as exc:
        login_limiter.record_failure(limiter_key)
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    login_limiter.record_success(limiter_key)
    return status_route(db)


@router.delete("", dependencies=[Depends(require_authenticated_admin)])
def deactivate_route(db: Session = Depends(get_db)) -> dict:
    activation_service.deactivate(db)
    return {"active": False}
