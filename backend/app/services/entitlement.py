"""Deployment mode + feature entitlement (ADR 0012).

The productization seam. A self-hosted instance runs in one of two modes:

* **free** — the default, and the whole self-host story. No ``LICENSE_SERVER_URL``
  is set, so the everyday app runs entirely locally and nothing phones home.
* **connected** — ``LICENSE_SERVER_URL`` points at the hosted PantryOS backend,
  which issues a signed entitlement unlocking premium, infrastructure-backed
  features (store integrations, cloud backup, remote relay).

Only ``core`` is granted in free mode. The premium features are deliberately the
ones that *require* server-side infrastructure to function — they cannot be
unlocked by patching a local flag, which is what makes them a defensible paid
tier rather than trivially-pirateable DRM.

This module is the CLIENT side of that seam. The hosted backend does not exist
yet, so connected mode currently FAILS SAFE to free: a half-configured instance
never crashes and never accidentally unlocks premium.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from fastapi import HTTPException

from app.core import settings

logger = logging.getLogger(__name__)

# --- Feature keys -----------------------------------------------------------
# "core" is the entire everyday app; a free instance is fully functional. The
# rest are infrastructure-backed and only work in connected mode.
FEATURE_CORE = "core"
FEATURE_STORE_INTEGRATIONS = "store_integrations"
FEATURE_CLOUD_AI = "cloud_ai"
FEATURE_CLOUD_BACKUP = "cloud_backup"
FEATURE_REMOTE_RELAY = "remote_relay"

ALL_FEATURES: tuple[str, ...] = (
    FEATURE_CORE,
    FEATURE_STORE_INTEGRATIONS,
    FEATURE_CLOUD_AI,
    FEATURE_CLOUD_BACKUP,
    FEATURE_REMOTE_RELAY,
)
FREE_FEATURES: tuple[str, ...] = (FEATURE_CORE,)

MODE_FREE = "free"
MODE_CONNECTED = "connected"

TIER_FREE = "free"
TIER_PREMIUM = "premium"


@dataclass(frozen=True)
class Entitlement:
    mode: str
    tier: str
    features: frozenset[str]
    valid: bool
    source: str
    reason: str

    def has(self, feature: str) -> bool:
        return feature in self.features


def deployment_mode() -> str:
    """Free unless a license server is configured. Never raises."""
    return MODE_CONNECTED if settings.license_server_url.strip() else MODE_FREE


def _free_tier(*, mode: str, source: str, reason: str) -> Entitlement:
    return Entitlement(
        mode=mode,
        tier=TIER_FREE,
        features=frozenset(FREE_FEATURES),
        valid=True,
        source=source,
        reason=reason,
    )


# resolve_entitlement() runs on premium-route hot paths, so the verified
# result is cached briefly. Activation/refresh/deactivation bust it.
_CACHE_TTL_SECONDS = 30.0
_cache: tuple[float, "Entitlement | None"] = (0.0, None)


def bust_cache() -> None:
    global _cache
    _cache = (0.0, None)


def resolve_entitlement() -> Entitlement:
    """What this instance is entitled to, right now.

    Free mode grants the full local app and nothing infrastructure-backed.
    Connected mode reads the persisted activation (``entitlement_state``) and
    verifies its signed token OFFLINE — per ADR 0018 this function never
    touches the network; the background refresher does. Any failure resolves
    to free tier, never an error.
    """
    if deployment_mode() == MODE_FREE:
        return _free_tier(
            mode=MODE_FREE,
            source="local",
            reason="Self-hosted free tier — all local features enabled.",
        )

    global _cache
    import time as _time

    cached_at, cached = _cache
    if cached is not None and _time.monotonic() - cached_at < _CACHE_TTL_SECONDS:
        return cached

    entitlement = _resolve_connected()
    _cache = (_time.monotonic(), entitlement)
    return entitlement


def _resolve_connected() -> Entitlement:
    try:
        from app.db.session import SessionLocal
        from app.services.activation import GRACE, get_state, verify_token

        with SessionLocal() as db:
            state = get_state(db)
        if state is None:
            return _free_tier(
                mode=MODE_CONNECTED,
                source="unactivated",
                reason="Connected mode configured but no activation key has been entered.",
            )
        verified = verify_token(state.token)
        if verified is None:
            return _free_tier(
                mode=MODE_CONNECTED,
                source="expired",
                reason=(
                    f"Activation …{state.key_hint} has expired past its grace window. "
                    "Local features are unaffected; renew to restore premium."
                ),
            )
        features = frozenset(FREE_FEATURES) | (frozenset(verified.features) & frozenset(ALL_FEATURES))
        if verified.in_grace:
            grace_until = (verified.expires_at + GRACE).date().isoformat()
            reason = (
                f"Activation …{state.key_hint} could not be renewed recently; premium continues "
                f"on the grace window until {grace_until}."
            )
            source = "grace"
        else:
            reason = f"Activation …{state.key_hint} verified locally."
            source = "activation"
        return Entitlement(
            mode=MODE_CONNECTED,
            tier=verified.tier,
            features=features,
            valid=True,
            source=source,
            reason=reason,
        )
    except Exception:  # noqa: BLE001 - entitlement must NEVER break the app
        logger.warning("entitlement resolution failed; falling back to free tier", exc_info=True)
        return _free_tier(
            mode=MODE_CONNECTED,
            source="error",
            reason="Entitlement could not be resolved — running as free tier.",
        )


def require_feature(feature: str):
    """FastAPI dependency factory: 402 unless the instance is entitled to ``feature``.

    Use on premium routes. In free mode, core passes and infra-backed features
    return a machine-readable 402 the frontend can turn into an upgrade prompt.
    """

    def _dependency() -> None:
        entitlement = resolve_entitlement()
        if not entitlement.has(feature):
            raise HTTPException(
                status_code=402,
                detail={
                    "error": "feature_not_available",
                    "feature": feature,
                    "mode": entitlement.mode,
                    "message": (
                        f"'{feature}' requires PantryOS Connect. "
                        "This instance is running in free mode."
                    ),
                },
            )

    return _dependency


def check_brokered_feature(feature: str) -> None:
    """Enforce entitlement on the brokered (premium) path only — ADR 0018 §3.

    BYO local credentials are always free, so callers invoke this only after
    finding no local credentials for a provider. If an integration broker is
    configured, the request is implicitly asking for the brokered path: raise
    the 402 unless entitled. With no broker configured either, do nothing —
    the caller falls through to its usual "not configured" error, exactly as
    in a pure self-host install.
    """
    from app.services.integration_broker import get_broker

    if get_broker().is_available():
        require_feature(feature)()
