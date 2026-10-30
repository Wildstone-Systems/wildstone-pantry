from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


class BootstrapAdminRequest(BaseModel):
    email: str
    password: str = Field(min_length=8)


class LoginRequest(BaseModel):
    email: str
    password: str


class AuthResponse(BaseModel):
    token: str
    expires_at: datetime
    user_id: UUID
    role: str


class UserRead(BaseModel):
    id: UUID
    email: str
    role: str
    is_active: bool
    created_at: datetime
