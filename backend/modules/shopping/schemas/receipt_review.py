"""Explicit receipt-review wire contract; advisory annotations carry no authority."""

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class DraftLine(BaseModel):
    model_config = ConfigDict(extra="ignore")
    line_id: str = Field(min_length=1, max_length=64)
    raw_line: str = Field(max_length=4000)
    # Editable excluded rows still carry immutable source binding. Included rows
    # receive the stricter trimmed-name ReceiptCommitLine validation on commit.
    name: str = Field(max_length=200)
    quantity: str = Field(max_length=40)
    unit: str = Field(max_length=16)
    matched_item_id: UUID | None = None
    create_new: bool = False
    role: Literal["stock", "financial_adjustment", "return", "unknown"] = "unknown"
    excluded: bool = False
    confirmed: bool = False


class DraftAction(BaseModel):
    token: str = Field(min_length=20, max_length=200)
    lines: list[DraftLine] = Field(min_length=1, max_length=200)


class ReceiptCandidateRead(BaseModel):
    id: UUID
    name: str


class ReceiptAnnotationRead(BaseModel):
    line_id: str
    binding: str
    status: Literal["rules", "limit", "disabled", "ok", "abstain", "unavailable", "invalid"]
    candidates: list[ReceiptCandidateRead]
    role: str | None = None
    suggested_item_id: UUID | None = None
    score: float | None = None
    role_score: float | None = None
    identity_score: float | None = None
    role_margin: float | None = None
    identity_margin: float | None = None
    below_threshold: bool = True
    threshold: float | None = None
    reason_code: str | None = None
    provider: str | None = None
    model: str | None = None
    evaluation_id: str | None = None


class ReceiptDraftLineRead(DraftLine):
    matched_item_name: str | None = None
    expiration_days_estimate: int | None = None
    source: str = "rules"
    confidence: float | None = None
    source_nonstock: bool = False
    annotation: ReceiptAnnotationRead | None = None


class ReceiptDraftRead(BaseModel):
    version: Literal["receipt-review.v1"]
    draft_id: UUID
    token: str
    location_id: UUID
    raw_text: str | None
    source_filename: str | None
    engine: str
    lines: list[ReceiptDraftLineRead]
    line_count: int
    warning: str


class ReceiptClassifierReviewRead(BaseModel):
    version: Literal["receipt-review.v1"]
    annotations: list[ReceiptAnnotationRead]
    provider_requests: int = Field(ge=0, le=8)
    status: Literal["unavailable", "stale_configuration", "stale_source"] | None = None
    warning: str
