"""AI provider status (ADR 0018 §9). Admin-readable; local providers only."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.security import require_authenticated_admin
from app.db import get_db
from modules.automation.providers import CAP_VISION, list_providers, resolve_provider

router = APIRouter(prefix="/ai", tags=["ai"], dependencies=[Depends(require_authenticated_admin)])


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
    }
