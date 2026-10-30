from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.security import require_authenticated_admin
from app.db import get_db
from app.schemas.household import (
    ProfileCreateRequest,
    ProfileListResponse,
    ProfileRead,
    ProfileUpdateRequest,
    SetPinRequest,
    VerifyPinRequest,
    VerifyPinResponse,
)
from app.services.profiles import (
    create_profile,
    list_profiles,
    set_profile_pin,
    update_profile,
    verify_profile_pin,
)

router = APIRouter(prefix="/household", tags=["household"])


# Reads are OPEN (the kiosk shows profiles to pick from — no login).
@router.get("/profiles", response_model=ProfileListResponse)
def list_profiles_route(
    include_inactive: bool = False,
    db: Session = Depends(get_db),
) -> ProfileListResponse:
    rows = list_profiles(db, include_inactive=include_inactive)
    return ProfileListResponse(profiles=[ProfileRead.model_validate(p) for p in rows])


# Writes are ADMIN-GATED (the protected surface — kids can't manage people).
@router.post("/profiles", response_model=ProfileRead, status_code=201)
def create_profile_route(
    payload: ProfileCreateRequest,
    _admin=Depends(require_authenticated_admin),
    db: Session = Depends(get_db),
) -> ProfileRead:
    try:
        profile = create_profile(
            db,
            display_name=payload.display_name,
            account_id=payload.account_id,
            daily_calorie_target=payload.daily_calorie_target,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return ProfileRead.model_validate(profile)


# Setting/clearing a PIN is ADMIN-gated; verifying one is OPEN (the kiosk unlock).
@router.put("/profiles/{profile_id}/pin", response_model=ProfileRead)
def set_pin_route(
    profile_id: UUID,
    payload: SetPinRequest,
    _admin=Depends(require_authenticated_admin),
    db: Session = Depends(get_db),
) -> ProfileRead:
    try:
        profile = set_profile_pin(db, profile_id=profile_id, pin=payload.pin)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return ProfileRead.model_validate(profile)


@router.post("/profiles/{profile_id}/verify-pin", response_model=VerifyPinResponse)
def verify_pin_route(
    profile_id: UUID,
    payload: VerifyPinRequest,
    db: Session = Depends(get_db),
) -> VerifyPinResponse:
    return VerifyPinResponse(valid=verify_profile_pin(db, profile_id=profile_id, pin=payload.pin))


@router.patch("/profiles/{profile_id}", response_model=ProfileRead)
def update_profile_route(
    profile_id: UUID,
    payload: ProfileUpdateRequest,
    _admin=Depends(require_authenticated_admin),
    db: Session = Depends(get_db),
) -> ProfileRead:
    fields = payload.model_fields_set
    try:
        profile = update_profile(
            db,
            profile_id=profile_id,
            display_name=payload.display_name,
            account_id=payload.account_id,
            daily_calorie_target=payload.daily_calorie_target,
            is_active=payload.is_active,
            account_id_set="account_id" in fields,
            calorie_target_set="daily_calorie_target" in fields,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return ProfileRead.model_validate(profile)
