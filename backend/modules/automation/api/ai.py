"""AI provider status + privacy consent (ADR 0018 §9).

Status is admin-readable; consent is the explicit per-household opt-in that
must exist before any image leaves the box for cloud AI. Local providers
need no consent — nothing leaves the machine.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.security import require_authenticated_admin
from app.db import get_db
from app.services.app_settings import TRUE, is_true, set_setting
from modules.automation.providers import CAP_VISION, list_providers, resolve_provider
from modules.automation.providers.wildstone import CONSENT_KEY

router = APIRouter(prefix="/ai", tags=["ai"], dependencies=[Depends(require_authenticated_admin)])


class ConsentRequest(BaseModel):
    allow: bool


@router.get("/status")
def status_route(db: Session = Depends(get_db)) -> dict:
    active = resolve_provider(db, CAP_VISION)
    return {
        "providers": [
            {
                "key": p.key,
                "label": p.label,
                "configured": p.configured(db),
                "capabilities": sorted(p.capabilities()),
            }
            for p in list_providers()
        ],
        "active_vision_provider": active.key if active else None,
        "cloud_consent": is_true(db, CONSENT_KEY),
    }


@router.post("/consent")
def consent_route(payload: ConsentRequest, db: Session = Depends(get_db)) -> dict:
    set_setting(db, CONSENT_KEY, TRUE if payload.allow else "false")
    return {"cloud_consent": payload.allow}
