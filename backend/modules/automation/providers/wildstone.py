"""Cloud AI provider (ADR 0018 §9): proxied through the hosted service.

The model-vendor API key never leaves the cloud (same principle as retailer
secrets); requests are instance-signed and require BOTH the ``cloud_ai``
entitlement and the household's explicit privacy consent — a receipt photo
never leaves the box without an affirmative opt-in (Q47 privacy decision).
Metering is server-side (credits, hard cap, no overage — Q47-1).
"""

from __future__ import annotations

import base64
import hashlib

import httpx
from sqlalchemy.orm import Session

from app.core import settings
from modules.automation.providers.base import (
    CAP_VISION,
    MAX_IMAGE_BYTES,
    AIUnavailable,
    ReceiptExtraction,
    parse_lines_payload,
)

CONSENT_KEY = "cloud_ai_consent"


def _consented(db: Session) -> bool:
    from app.services.app_settings import is_true

    return is_true(db, CONSENT_KEY)


def _entitled() -> bool:
    from app.services.entitlement import FEATURE_CLOUD_AI, resolve_entitlement

    return resolve_entitlement().has(FEATURE_CLOUD_AI)


class WildstoneProvider:
    key = "wildstone"
    label = "PantryOS Connect cloud AI"

    def capabilities(self) -> set[str]:
        return {CAP_VISION}

    def configured(self, db: Session) -> bool:
        return bool(settings.integration_broker_url.strip()) and _entitled() and _consented(db)

    def extract_receipt(self, db: Session, image: bytes, *, media_type: str) -> ReceiptExtraction:
        if len(image) > MAX_IMAGE_BYTES:
            raise AIUnavailable("Image is too large for AI processing (8 MB max).")
        if not settings.integration_broker_url.strip():
            raise AIUnavailable("Cloud AI requires PantryOS Connect (no broker configured).")
        if not _entitled():
            raise AIUnavailable("Cloud AI requires an active PantryOS Connect subscription.")
        if not _consented(db):
            raise AIUnavailable(
                "Cloud AI is off: sending images off this device requires the household's "
                "explicit opt-in (Admin → AI)."
            )

        from app.services.activation import get_state
        from app.services.integration_broker import HttpBroker

        if get_state(db) is None:
            raise AIUnavailable("This instance is not activated.")
        broker = HttpBroker(settings.integration_broker_url)
        digest = hashlib.sha256(image).hexdigest()
        payload = broker._signed_payload(db, digest)  # noqa: SLF001 - same package family
        payload["image_b64"] = base64.b64encode(image).decode()
        payload["media_type"] = media_type
        try:
            resp = httpx.post(f"{broker.base_url}/v1/ai/receipt", json=payload, timeout=90)
        except httpx.HTTPError as exc:
            raise AIUnavailable(f"Cloud AI is unreachable: {exc}") from exc
        if resp.status_code == 402:
            raise AIUnavailable("Cloud AI requires an active subscription.")
        if resp.status_code == 429:
            raise AIUnavailable("This month's cloud AI allowance is used up — falls back to local/rules.")
        if resp.status_code >= 400:
            raise AIUnavailable(f"Cloud AI error ({resp.status_code}).")
        return parse_lines_payload(self.key, resp.json())
