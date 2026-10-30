"""Entitlement + deployment-mode status (ADR 0012).

Ungated like ``/license/status`` so the frontend can read it up front and decide
which premium surfaces to show. Reports what this instance is entitled to and
whether store integrations are brokered.
"""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from app.services.entitlement import ALL_FEATURES, resolve_entitlement
from app.services.integration_broker import get_broker

router = APIRouter(prefix="/entitlement", tags=["entitlement"])


class EntitlementResponse(BaseModel):
    mode: str
    tier: str
    features: list[str]
    all_features: list[str]
    valid: bool
    source: str
    reason: str
    integrations_brokered: bool


@router.get("", response_model=EntitlementResponse)
def get_entitlement_route() -> EntitlementResponse:
    entitlement = resolve_entitlement()
    return EntitlementResponse(
        mode=entitlement.mode,
        tier=entitlement.tier,
        features=sorted(entitlement.features),
        all_features=sorted(ALL_FEATURES),
        valid=entitlement.valid,
        source=entitlement.source,
        reason=entitlement.reason,
        integrations_brokered=get_broker().is_available(),
    )
