"""Device pairing + management (ADR 0018 §8).

Minting pairing codes and managing devices is real-admin-only; the pair
endpoint itself is open (the phone has no credential yet — the one-time code
IS the credential) and failure-rate-limited.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.ratelimit import login_limiter
from app.core.security import require_authenticated_admin
from app.db import get_db
from app.services import device_keys

router = APIRouter(prefix="/devices", tags=["devices"])


class PairingCodeResponse(BaseModel):
    code: str
    expires_in: int


class PairRequest(BaseModel):
    code: str = Field(min_length=4, max_length=64)
    device_name: str = Field(default="Device", max_length=120)


class DeviceRead(BaseModel):
    id: uuid.UUID
    name: str
    user_id: uuid.UUID
    created_at: str
    last_used_at: str | None
    revoked: bool


def _read(row) -> DeviceRead:
    return DeviceRead(
        id=row.id,
        name=row.name,
        user_id=row.user_id,
        created_at=row.created_at.isoformat() if row.created_at else "",
        last_used_at=row.last_used_at.isoformat() if row.last_used_at else None,
        revoked=row.revoked_at is not None,
    )


@router.post("/pairing-codes", response_model=PairingCodeResponse)
def mint_code_route(admin=Depends(require_authenticated_admin)) -> PairingCodeResponse:
    # v1: devices bind to the admin who minted the code (per-member codes come
    # with the fuller user-management pass).
    code, ttl = device_keys.mint_pairing_code(admin.id)
    return PairingCodeResponse(code=code, expires_in=ttl)


@router.post("/pair")
def pair_route(payload: PairRequest, request: Request, db: Session = Depends(get_db)) -> dict:
    client_host = request.client.host if request.client else "unknown"
    limiter_key = f"pairing:{client_host}"
    login_limiter.check(limiter_key)
    result = device_keys.pair(db, code=payload.code, device_name=payload.device_name)
    if result is None:
        login_limiter.record_failure(limiter_key)
        raise HTTPException(status_code=401, detail="Invalid or expired pairing code.")
    login_limiter.record_success(limiter_key)
    plaintext, row = result
    # The plaintext key is shown exactly once — the device stores it.
    return {"device_key": plaintext, "device": _read(row).model_dump(mode="json")}


@router.get("", dependencies=[Depends(require_authenticated_admin)])
def list_route(db: Session = Depends(get_db)) -> dict:
    return {"devices": [_read(r).model_dump(mode="json") for r in device_keys.list_devices(db)]}


@router.delete("/{device_id}", dependencies=[Depends(require_authenticated_admin)])
def revoke_route(device_id: uuid.UUID, db: Session = Depends(get_db)) -> dict:
    if not device_keys.revoke(db, device_id):
        raise HTTPException(status_code=404, detail="No such active device.")
    return {"revoked": True}


@router.post("/revoke-all", dependencies=[Depends(require_authenticated_admin)])
def revoke_all_route(db: Session = Depends(get_db)) -> dict:
    return {"revoked": device_keys.revoke_all(db)}
