from __future__ import annotations

from urllib.parse import urlsplit

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Values that are fine for local development but must never be used in a
# protected (staging/production) environment.
_INSECURE_PASSWORDS = {"TestPass123!", "admin123", "password", "changeme"}
_PROTECTED_ENVIRONMENTS = {"staging", "production"}
# Default development database credentials. These must be rejected in a protected
# env REGARDLESS OF HOST — the committed compose file points the same
# pantryos/pantryos pair at the `postgres` host, so a host-specific allowlist
# would let staging/production start on them (ported from GH #13).
_INSECURE_DB_CREDENTIALS = {("pantryos", "pantryos")}


def _uses_insecure_db_credentials(database_url: str) -> bool:
    """True if the URL carries a known default dev username/password pair."""
    try:
        parts = urlsplit(database_url)
    except ValueError:
        return False
    return (parts.username, parts.password) in _INSECURE_DB_CREDENTIALS


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Deployment environment. In protected envs the app refuses to start on
    # insecure defaults (see _validate_protected_env).
    environment: str = "development"
    app_version: str = "0.1.0"
    # Backups (ADR 0018 §6). Empty = disabled. The product compose mounts a
    # named volume at /backups and sets this; the nightly scheduler and the
    # admin "back up now" button both write pg_dump custom-format archives
    # (or a file copy for SQLite) into it, pruned to 7 daily + 4 weekly.
    backup_dir: str = ""
    # Update-manifest feed (ADR 0018 §6). Empty = never checks (free-mode
    # instances make no unsolicited network calls). Product installs point at
    # the GitHub Releases manifest; /api/system/version reports what it finds.
    update_manifest_url: str = ""
    # Product image only (ADR 0018 §4): path to the built frontend (Vite dist/)
    # served by the backend itself for single-origin deployments. Empty (the
    # dev default) = the backend serves the API only and Vite serves the UI.
    frontend_dist: str = ""

    database_url: str = "postgresql+psycopg://pantryos:pantryos@localhost:5432/pantryos"
    # Browser origins allowed to call the API cross-origin, comma-separated. The
    # bundled frontend proxies /api same-origin and needs none of these; they
    # exist for clients served from another host (e.g. a tablet over the LAN).
    cors_allow_origins: str = "http://localhost:5173,http://127.0.0.1:5173"
    # Personal-mode toggle. When disabled, the backend will not require bearer tokens and will
    # auto-select (or create) a local admin user for request context.
    auth_enabled: bool = True
    auth_personal_email: str = "admin@pantryos.local"
    # Only used when AUTH_ENABLED=false and the backend must create the personal user.
    # This keeps it possible to later re-enable auth and login without wiping the DB.
    auth_personal_password: str = "TestPass123!"
    auth_token_ttl_hours: int = 24
    # NOTE (ADR 0018): the legacy LICENSE_KEY / ENFORCE_LICENSE shared-password
    # gate is removed. Lingering env values are ignored (extra="ignore") and
    # trigger a startup deprecation warning in app.main. Use AUTH_ENABLED for
    # access control; activation keys (ADR 0018) are unrelated.
    # In-app feedback is stored locally only; there is no outbound feedback
    # intake. Lingering WEBHOOK_* / FEEDBACK_WEBHOOK_* env values are ignored.

    # Home Assistant outbound webhook (ADR 0011). Inventory events are POSTed,
    # HMAC-signed, to this URL (e.g. http://homeassistant:8123/api/webhook/pantryos).
    # These env values are the FALLBACK seed; the admin Integrations screen stores
    # an encrypted in-app override that wins at publish time (ADR 0009).
    ha_webhook_url: str = ""
    ha_webhook_secret: str = ""

    # Wildstone Home Lab (ADR 0026). Set by Wildstone when it installs PantryOS
    # (the managed shape); a separately hosted PantryOS is connected from the
    # admin screen instead, whose stored value wins (ADR 0009).
    wildstone_keystone_url: str = ""
    wildstone_publisher_token: str = ""
    # Where a person opens PantryOS's web UI (not the API address), e.g.
    # https://pantry.home.arpa. Handed to Wildstone at pairing so its family
    # dashboard can link here. The backend can't know where its frontend is
    # served, so it is never guessed: unset means Wildstone isn't told.
    pantryos_public_url: str = ""

    # Vendor integrations (step 5). Kroger Cart API (OUT) — dormant until a
    # self-serve developer key is configured; empty = the connector reports
    # "not configured" and is skipped.
    kroger_client_id: str = ""
    kroger_client_secret: str = ""
    # Redirect URI registered in the Kroger dev portal for the Cart API's
    # authorization_code flow (Phase 4, ADR 0013). Must exactly match the portal
    # entry, e.g. https://<host>/api/integrations/kroger/oauth/callback.
    kroger_redirect_uri: str = ""

    # Purchase intake over IMAP (step 5b, IN). Parse order-confirmation emails
    # from the family's own mailbox — no Gmail API / Google CASA burden. These env
    # values are the FALLBACK/seed; the in-app admin Integrations screen stores an
    # override in the DB (encrypted) that takes precedence at request time (ADR 0009).
    imap_host: str = ""
    imap_port: int = 993
    imap_username: str = ""
    imap_password: str = ""
    imap_mailbox: str = "INBOX"

    # Symmetric key (Fernet, urlsafe-base64) used to encrypt connector credentials
    # stored in the DB via the admin Integrations screen (ADR 0009). Empty = the app
    # refuses to persist secrets in-app (env-only config still works). Generate with
    # `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`.
    config_secret_key: str = ""

    # Productization seam (ADR 0012). Both empty = "free mode": a fully-functional
    # self-hosted instance where everything runs locally and nothing phones home.
    # Point these at the hosted PantryOS backend to move an instance into
    # "connected mode" — remote entitlement (premium tiers) and OAuth brokering
    # for store integrations — WITHOUT shipping any third-party client secrets.
    license_server_url: str = ""
    integration_broker_url: str = ""
    # Ed25519 public key(s) for verifying entitlement tokens OFFLINE, comma-
    # separated urlsafe-base64 (ADR 0018 §2). Ships with the product build;
    # accepts two entries during signer rotation.
    license_public_keys: str = ""

    # USDA FoodData Central — a second barcode source for US branded groceries
    # that Open Food Facts often misses (public-domain data). Dormant until set;
    # get a free key at https://api.data.gov/signup/ .
    usda_fdc_api_key: str = ""

    # Spoonacular — a second recipe-search provider alongside the always-on,
    # keyless TheMealDB (small catalog). Dormant until set; free key at
    # https://spoonacular.com/food-api (50 points/day on the free tier).
    spoonacular_api_key: str = ""

    @model_validator(mode="after")
    def _validate_protected_env(self) -> Settings:
        if self.environment.lower() not in _PROTECTED_ENVIRONMENTS:
            return self
        problems: list[str] = []
        if _uses_insecure_db_credentials(self.database_url):
            problems.append("DATABASE_URL uses the default development credentials")
        if self.auth_enabled and self.auth_personal_password in _INSECURE_PASSWORDS:
            problems.append("AUTH_PERSONAL_PASSWORD is a known-insecure default")
        if problems:
            raise ValueError(
                f"Refusing to start in '{self.environment}' with insecure configuration: "
                + "; ".join(problems)
            )
        return self

    def cors_allow_origin_list(self) -> list[str]:
        """CORS origins as a list (parsed from the comma-separated setting)."""
        return [origin.strip() for origin in self.cors_allow_origins.split(",") if origin.strip()]


settings = Settings()
