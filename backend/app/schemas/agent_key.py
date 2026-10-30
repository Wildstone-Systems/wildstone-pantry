from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class AgentKeyCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    scopes: str = Field(default="read,write", max_length=255)


class AgentKeyRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    token_prefix: str
    scopes: str
    revoked: bool
    created_at: datetime
    last_used_at: datetime | None


class AgentKeyCreatedResponse(BaseModel):
    """Returned once on creation — includes the plaintext secret."""

    key: AgentKeyRead
    secret: str


class AgentKeyListResponse(BaseModel):
    keys: list[AgentKeyRead]
