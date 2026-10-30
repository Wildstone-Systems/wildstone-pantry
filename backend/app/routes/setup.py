"""First-run setup wizard API (ADR 0018 §7).

Open by design while setup is incomplete — the wizard runs before any admin
exists, exactly like ``/api/auth/bootstrap-admin``. Every mutating endpoint
here returns 403 once setup is completed, so a lived-in instance exposes no
open reconfiguration surface. ``GET /status`` stays open forever (the SPA
reads it on boot to decide whether to show the wizard).

Persistence lives in ``app.services.setup`` (route → service, AGENTS.md).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.db import get_db
from app.services.app_settings import (
    KEY_AUTH_ENABLED_OVERRIDE,
    KEY_HOUSEHOLD_NAME,
    KEY_LAN_OPEN_ACK,
    KEY_SETUP_COMPLETED,
    TRUE,
    get_setting,
    is_true,
    set_setting,
)
from app.services.auth import needs_admin_setup
from app.services.setup import seed_preset, setup_completed

router = APIRouter(prefix="/setup", tags=["setup"])


class SetupStatus(BaseModel):
    completed: bool
    needs_admin: bool
    household_name: str | None
    accounts_mode: bool


class HouseholdRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)


class ModeRequest(BaseModel):
    mode: str = Field(pattern="^(open|accounts)$")
    lan_ack: bool = False


class SeedRequest(BaseModel):
    preset: str = Field(pattern="^(minimal|typical)$")


def _require_setup_open(db: Session = Depends(get_db)) -> Session:
    if setup_completed(db):
        raise HTTPException(status_code=403, detail="Setup is already completed.")
    return db


@router.get("/status", response_model=SetupStatus)
def status_route(db: Session = Depends(get_db)) -> SetupStatus:
    return SetupStatus(
        completed=setup_completed(db),
        needs_admin=needs_admin_setup(db),
        household_name=get_setting(db, KEY_HOUSEHOLD_NAME),
        accounts_mode=is_true(db, KEY_AUTH_ENABLED_OVERRIDE),
    )


@router.post("/household")
def household_route(payload: HouseholdRequest, db: Session = Depends(_require_setup_open)) -> dict:
    set_setting(db, KEY_HOUSEHOLD_NAME, payload.name.strip())
    return {"household_name": payload.name.strip()}


@router.post("/mode")
def mode_route(payload: ModeRequest, db: Session = Depends(_require_setup_open)) -> dict:
    if payload.mode == "accounts":
        set_setting(db, KEY_AUTH_ENABLED_OVERRIDE, TRUE)
    else:
        # Open household mode on the LAN requires the explicit acknowledgment
        # (owner decision on ADR 0018 Q43-2): no silent everyone-is-admin.
        if not payload.lan_ack:
            raise HTTPException(
                status_code=400,
                detail="Open mode requires acknowledging that anyone on the network has full access.",
            )
        set_setting(db, KEY_LAN_OPEN_ACK, TRUE)
    return {"mode": payload.mode}


@router.post("/seed-defaults")
def seed_route(payload: SeedRequest, db: Session = Depends(_require_setup_open)) -> dict:
    return {"preset": payload.preset, "created": seed_preset(db, payload.preset)}


@router.post("/complete")
def complete_route(db: Session = Depends(get_db)) -> dict:
    # Idempotent and allowed even mid-flow ("skip setup"); never 403s so the
    # wizard can always be dismissed.
    set_setting(db, KEY_SETUP_COMPLETED, TRUE)
    return {"completed": True}
