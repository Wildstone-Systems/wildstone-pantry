from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class ConnectorConfigView(BaseModel):
    """Redacted config for the admin UI — never includes secret values."""

    key: str
    values: dict[str, str]  # non-secret fields (host, port, username, mailbox)
    secret_set: dict[str, bool]  # secret field name -> whether a value is present
    has_override: bool  # a DB override row exists
    from_env_only: bool  # config currently comes only from the environment
    secrets_available: bool  # CONFIG_SECRET_KEY is set on this deployment


class ConnectorConfigUpdate(BaseModel):
    """Partial update. Omit a secret (or send "") to keep the current value."""

    updates: dict[str, str] = Field(default_factory=dict)


class ConnectorTestResult(BaseModel):
    ok: bool
    detail: str


class WebhookDeliveryRead(BaseModel):
    """One outbound-webhook delivery outcome (ADR 0011 observability)."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    event_type: str
    status: str  # delivered | failed
    attempts: int
    last_attempt_at: datetime | None
    delivered_at: datetime | None
    created_at: datetime
