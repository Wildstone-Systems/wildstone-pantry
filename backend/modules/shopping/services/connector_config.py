"""Effective connector configuration: DB override over env fallback (ADR 0009).

Each connector declares its fields (name, whether it's secret, and the env
fallback). The admin Integrations screen writes an encrypted override into
``connector_config``; ``get_effective`` merges that override on top of the env
seed so a value set in-app wins at request time (no restart), while unset fields
fall back to the environment. Secrets are never returned to clients — only a
redacted public view (non-secret values + "is a secret set?" flags).
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.crypto import decrypt_json, encrypt_json, secrets_available
from app.models import ConnectorConfig


@dataclass(frozen=True)
class Field:
    name: str
    secret: bool = False
    # Callable returning the env fallback value as a string ("" when unset).
    env_default: str = ""


# Field specs per connector key. IMAP is the only one wired to in-app config for
# now; add a connector here to make it admin-configurable.
_SPECS: dict[str, list[Field]] = {
    "classifier": [
        Field("enabled"),
        Field("provider"),
        Field("model"),
        Field("execution_location"),
        Field("endpoint"),
        Field("cloud_opt_in"),
        Field("api_key", secret=True),
    ],
    "imap": [
        Field("host"),
        Field("port"),
        Field("username"),
        Field("password", secret=True),
        Field("mailbox"),
    ],
    # Outbound inventory-event webhook for Home Assistant (ADR 0011). The URL is
    # not secret (it's visible in HA anyway); the HMAC signing secret is.
    "home_assistant": [
        Field("webhook_url"),
        Field("secret", secret=True),
    ],
    # Barcode/product-data sources (ADR 0013). Each deployment can put in its own
    # keys in free mode; the productized broker supplies them later. Open Food
    # Facts needs no key, so it has no entry here.
    "usda": [
        Field("api_key", secret=True),
    ],
    # Recipe search (alongside the always-on, keyless TheMealDB). Dormant until
    # a key is set — free tier at spoonacular.com/food-api.
    "spoonacular": [
        Field("api_key", secret=True),
    ],
    "kroger": [
        Field("client_id"),
        Field("client_secret", secret=True),
        # Optional Kroger store id — set it to get prices in search results.
        Field("location_id"),
        # Cart API (Phase 4) redirect URI, registered in the Kroger dev portal.
        Field("redirect_uri"),
    ],
    # Instacart Developer Platform — multi-retailer shoppable-list link (ADR 0013
    # Phase 4b). DORMANT until an API key is set (Instacart isn't accepting new
    # developer applications yet). base_url lets you point at the dev server while
    # testing before flipping to production.
    "instacart": [
        Field("api_key", secret=True),
        Field("base_url"),
    ],
    # Local AI providers (ADR 0018 §9): free, no entitlement, dormant until
    # configured. Local always wins over cloud AI (Q47-2).
    "ollama": [
        Field("base_url"),
        Field("model"),
    ],
    "openai_compat": [
        Field("base_url"),
        Field("api_key", secret=True),
        Field("model"),
    ],
    # Native OIDC SSO (ADR 0020): DB-only, admin-configured. Defaults for blank
    # fields (scopes, groups_claim, users_group, admin_group, button_label) are
    # applied in app.services.oidc.effective_config, not here.
    # Wildstone Home Lab (ADR 0026): where the household event intake is, and
    # the publisher token Keystone minted for the `pantry` capability.
    "wildstone": [
        Field("keystone_url"),
        Field("publisher_token", secret=True),
    ],
    "oidc": [
        Field("issuer"),
        Field("client_id"),
        Field("client_secret", secret=True),
        Field("scopes"),
        Field("groups_claim"),
        Field("users_group"),
        Field("admin_group"),
        Field("button_label"),
    ],
}


def _env_seed(key: str) -> dict[str, str]:
    if key == "imap":
        return {
            "host": settings.imap_host,
            "port": str(settings.imap_port),
            "username": settings.imap_username,
            "password": settings.imap_password,
            "mailbox": settings.imap_mailbox or "INBOX",
        }
    if key == "home_assistant":
        return {
            "webhook_url": settings.ha_webhook_url,
            "secret": settings.ha_webhook_secret,
        }
    if key == "usda":
        return {"api_key": settings.usda_fdc_api_key}
    if key == "spoonacular":
        return {"api_key": settings.spoonacular_api_key}
    if key == "kroger":
        return {
            "client_id": settings.kroger_client_id,
            "client_secret": settings.kroger_client_secret,
            "location_id": "",  # DB-only; no env seed
            "redirect_uri": settings.kroger_redirect_uri,
        }
    if key == "instacart":
        return {"api_key": "", "base_url": ""}  # DB-only (admin-configured)
    if key == "wildstone":
        return {
            "keystone_url": settings.wildstone_keystone_url,
            "publisher_token": settings.wildstone_publisher_token,
        }
    if key == "oidc":
        return {}  # DB-only (admin-configured); no env vars for SSO (ADR 0020)
    return {}


def fields(key: str) -> list[Field]:
    return list(_SPECS.get(key, []))


def is_configurable(key: str) -> bool:
    return key in _SPECS


def _stored(db: Session, key: str) -> dict[str, str]:
    row = db.get(ConnectorConfig, key)
    if row is None:
        return {}
    return {str(k): ("" if v is None else str(v)) for k, v in decrypt_json(row.ciphertext).items()}


def get_effective(db: Session, key: str) -> dict[str, str]:
    """Effective config = env seed with the stored (decrypted) override on top.

    Only non-empty stored values override the env seed, so clearing a field in
    the DB falls back to the environment rather than to blank.
    """
    merged = dict(_env_seed(key))
    for k, v in _stored(db, key).items():
        if v != "":
            merged[k] = v
    return merged


def get_classifier_config(db: Session) -> dict[str, str]:
    """DB-only classifier values without the legacy connector string coercion."""
    row = db.get(ConnectorConfig, "classifier")
    values = decrypt_json(row.ciphertext) if row else {}
    if not isinstance(values, dict) or any(not isinstance(value, str) for value in values.values()):
        raise ValueError("Invalid saved classifier configuration")
    return values


def classifier_revision(db: Session) -> str:
    """Opaque stored ciphertext revision for request budgets; never a key hash."""
    import hashlib

    row = db.get(ConnectorConfig, "classifier")
    return hashlib.sha256(row.ciphertext.encode()).hexdigest() if row else "not_configured"


def classifier_snapshot(db: Session, *, lock: bool = False) -> dict:
    """Atomic values/revision read; the values are private to automation services."""
    import hashlib

    if lock:
        if db.get_bind().dialect.name == "postgresql":
            # Table lock covers the absent-row case and blocks normal config writes
            # until the caller atomically commits its human review receipt.
            db.execute(text("LOCK TABLE connector_config IN SHARE MODE"))
        else:
            db.execute(
                text("UPDATE connector_config SET ciphertext = ciphertext WHERE key = :key"), {"key": "classifier"}
            )
    ciphertext = db.execute(
        select(ConnectorConfig.ciphertext).where(ConnectorConfig.key == "classifier")
    ).scalar_one_or_none()
    values = decrypt_json(ciphertext) if ciphertext else {}
    if not isinstance(values, dict) or any(not isinstance(value, str) for value in values.values()):
        raise ValueError("Invalid classifier configuration")
    return {
        "values": values,
        "revision": hashlib.sha256(ciphertext.encode()).hexdigest() if ciphertext else "not_configured",
    }


def get_public(db: Session, key: str) -> dict:
    """Redacted view for the admin UI: non-secret values + secret-set flags.

    Reports whether config comes from the DB override and whether secret
    encryption is even available on this deployment.
    """
    spec = _SPECS.get(key, [])
    effective = get_effective(db, key)
    stored = _stored(db, key)

    values: dict[str, str] = {}
    secret_set: dict[str, bool] = {}
    for f in spec:
        if f.secret:
            secret_set[f.name] = bool(effective.get(f.name, ""))
        else:
            values[f.name] = effective.get(f.name, "")

    return {
        "key": key,
        "values": values,
        "secret_set": secret_set,
        "has_override": db.get(ConnectorConfig, key) is not None,
        "from_env_only": not stored,
        "secrets_available": secrets_available(),
    }


def set_config(db: Session, key: str, updates: dict[str, str]) -> None:
    """Merge ``updates`` into the stored override and persist it encrypted.

    Partial updates are supported: fields not present are left unchanged, so an
    admin can change the host without re-entering the password. A secret sent as
    empty string is treated as "leave as-is"; omit it to keep the current value.
    """
    if key == "classifier":
        raise LookupError("Use the dedicated classifier settings service")
    if key not in _SPECS:
        raise LookupError(f"Connector '{key}' is not configurable")

    spec_names = {f.name for f in _SPECS[key]}
    secret_names = {f.name for f in _SPECS[key] if f.secret}

    current = _stored(db, key)
    for name, value in updates.items():
        if name not in spec_names:
            continue
        if name in secret_names and value == "":
            continue  # empty secret = keep existing
        current[name] = value

    row = db.get(ConnectorConfig, key)
    ciphertext = encrypt_json(current)
    if row is None:
        db.add(ConnectorConfig(key=key, ciphertext=ciphertext))
    else:
        row.ciphertext = ciphertext
    db.commit()


def set_classifier_config(db: Session, updates: dict) -> None:
    """Atomic, validated classifier-only update; never reuse a key across identities.

    Automation owns the typed contract; shopping owns the encrypted table. This
    narrow public interface keeps both the validation and secret rotation atomic.
    """
    from modules.automation.classifier_contract import ClassifierConfig, ConfigUpdate, decode_config

    update = ConfigUpdate.model_validate(updates).model_dump(exclude_unset=True)
    current = decode_config(get_classifier_config(db))
    merged = current.model_dump()
    merged["api_key"] = current.api_key
    identity = ("provider", "execution_location", "endpoint", "model")
    if any(name in update and update[name] != merged[name] for name in identity):
        merged["api_key"] = ""
    if "provider" in update and update["provider"] != current.provider:
        # Consent belongs to the destination provider; a switch requires a second,
        # explicit save after the new provider has been selected.
        update["cloud_opt_in"] = False
    if not update.get("api_key"):
        update.pop("api_key", None)
    merged.update(update)
    valid = ClassifierConfig.model_validate(merged)
    values = valid.model_dump()
    values["api_key"] = valid.api_key
    stored = {name: str(value).lower() if isinstance(value, bool) else str(value) for name, value in values.items()}
    ciphertext = encrypt_json(stored)
    row = db.get(ConnectorConfig, "classifier")
    if row is None:
        db.add(ConnectorConfig(key="classifier", ciphertext=ciphertext))
    else:
        row.ciphertext = ciphertext
    db.commit()


def clear_config(db: Session, key: str) -> None:
    """Remove the in-app override; the connector reverts to env config."""
    row = db.get(ConnectorConfig, key)
    if row is not None:
        db.delete(row)
        db.commit()
