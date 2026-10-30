import uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.ratelimit import login_limiter
from app.core.security import require_admin, require_authenticated_admin, require_user
from app.db import get_db
from app.schemas.auth import AuthResponse, BootstrapAdminRequest, LoginRequest, UserRead
from app.services import app_settings
from app.services.auth import (
    authenticate,
    bootstrap_admin,
    create_user,
    list_users,
    needs_admin_setup,
    set_user_password,
)

router = APIRouter(prefix="/auth", tags=["auth"])


class SetupStatusResponse(BaseModel):
    needs_setup: bool


@router.get("/setup-status", response_model=SetupStatusResponse)
def setup_status_route(db: Session = Depends(get_db)) -> SetupStatusResponse:
    """Open: whether first-run admin setup is still needed (no provisioned admin)."""
    return SetupStatusResponse(needs_setup=needs_admin_setup(db))


@router.post("/bootstrap-admin", response_model=UserRead)
def bootstrap_admin_route(payload: BootstrapAdminRequest, db: Session = Depends(get_db)) -> UserRead:
    try:
        user = bootstrap_admin(db, email=payload.email, password=payload.password)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    return UserRead(id=user.id, email=user.email, role=user.role, is_active=user.is_active, created_at=user.created_at)


@router.post("/login", response_model=AuthResponse)
def login_route(payload: LoginRequest, request: Request, db: Session = Depends(get_db)) -> AuthResponse:
    # Failure-counting limiter (ADR 0018): brute force is throttled per
    # (client, email); successful logins are never limited.
    client_host = request.client.host if request.client else "unknown"
    limiter_key = f"{client_host}:{payload.email.strip().lower()}"
    login_limiter.check(limiter_key)
    try:
        user, token = authenticate(db, email=payload.email, password=payload.password)
    except LookupError as exc:
        login_limiter.record_failure(limiter_key)
        raise HTTPException(status_code=401, detail=str(exc)) from exc

    login_limiter.record_success(limiter_key)
    return AuthResponse(token=token.token, expires_at=token.expires_at, user_id=user.id, role=user.role)


class LocalLoginVisibilityRequest(BaseModel):
    hidden: bool


@router.put("/local-login-visibility")
def set_local_login_visibility_route(
    payload: LocalLoginVisibilityRequest,
    _admin=Depends(require_authenticated_admin),
    db: Session = Depends(get_db),
) -> dict:
    """Cosmetic toggle only (ADR 0020 Decision 4) — /api/auth/login is never disabled."""
    app_settings.set_setting(db, app_settings.KEY_LOCAL_LOGIN_HIDDEN, "true" if payload.hidden else "false")
    return {"hidden": payload.hidden}


@router.get("/me", response_model=UserRead)
def me_route(user=Depends(require_user)) -> UserRead:
    return UserRead(id=user.id, email=user.email, role=user.role, is_active=user.is_active, created_at=user.created_at)


@router.get("/users", response_model=list[UserRead])
def list_users_route(_admin=Depends(require_admin), db: Session = Depends(get_db)) -> list[UserRead]:
    rows = list_users(db)
    return [UserRead(id=u.id, email=u.email, role=u.role, is_active=u.is_active, created_at=u.created_at) for u in rows]


class CreateUserRequest(BaseModel):
    email: str
    password: str
    role: str = "member"


class SetPasswordRequest(BaseModel):
    password: str


@router.post("/users", response_model=UserRead)
def create_user_route(
    payload: CreateUserRequest,
    _admin=Depends(require_authenticated_admin),
    db: Session = Depends(get_db),
) -> UserRead:
    """Minimal user management (ADR 0018 §8, owner decision Q44-3): the admin
    creates named accounts for family members — no invites, no email flows.
    Device keys bind to these users so a phone is *someone*, not the admin."""
    try:
        user = create_user(db, email=payload.email, password=payload.password, role=payload.role)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return UserRead(id=user.id, email=user.email, role=user.role, is_active=user.is_active, created_at=user.created_at)


@router.post("/users/{user_id}/password")
def set_password_route(
    user_id: uuid.UUID,
    payload: SetPasswordRequest,
    _admin=Depends(require_authenticated_admin),
    db: Session = Depends(get_db),
) -> dict:
    try:
        updated = set_user_password(db, user_id=user_id, password=payload.password)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not updated:
        raise HTTPException(status_code=404, detail="No such user.")
    return {"updated": True}


@router.post("/password")
def change_own_password_route(
    payload: SetPasswordRequest,
    user=Depends(require_user),
    db: Session = Depends(get_db),
) -> dict:
    """Self-service password change for any signed-in account."""
    try:
        updated = set_user_password(db, user_id=user.id, password=payload.password)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not updated:
        raise HTTPException(status_code=404, detail="No such user.")
    return {"updated": True}
