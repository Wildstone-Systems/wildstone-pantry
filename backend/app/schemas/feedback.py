from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class FeedbackCreate(BaseModel):
    category: str = Field(default="bug", max_length=24)
    route: str | None = Field(default=None, max_length=256)
    message: str = Field(..., min_length=1)


class FeedbackRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    user_id: UUID
    category: str
    route: str | None
    message: str
    status: str
    created_at: datetime


class FeedbackListResponse(BaseModel):
    feedback: list[FeedbackRead]


class FeedbackUpdate(BaseModel):
    status: str = Field(..., max_length=24)
