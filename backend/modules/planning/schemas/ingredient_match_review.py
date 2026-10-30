"""Closed human-review commands: no generated numbers or verification claims."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, StrictBool, field_validator

from modules.automation.classifier_contract import StrictModel
from modules.catalog.schemas.match_evidence import MatchCandidate, MatchItemEvidence


class EvidenceFacts(StrictModel):
    base_ingredient: str | None = Field(default=None, min_length=1, max_length=200)
    identity: str | None = Field(default=None, min_length=1, max_length=200)
    form: str | None = Field(default=None, min_length=1, max_length=200)
    preparation: str | None = Field(default=None, min_length=1, max_length=200)
    composition: str | None = Field(default=None, min_length=1, max_length=1000)
    recipe_role: str | None = Field(default=None, min_length=1, max_length=500)

    @field_validator("*")
    @classmethod
    def unknown_is_missing(cls, value):
        if value is None:
            return None
        value = value.strip()
        return None if not value or value.lower() in {"unknown", "?", "n/a", "none", "not sure", "unsure"} else value


class CreateMatchReview(StrictModel):
    candidate_item_id: UUID
    facts: EvidenceFacts = Field(default_factory=EvidenceFacts)


class ReviewBinding(StrictModel):
    revision: int = Field(strict=True, ge=0)
    evidence_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    confirmation_token: str = Field(min_length=32, max_length=128)


class ConfirmMatchReview(ReviewBinding):
    confirm: StrictBool

    @field_validator("confirm")
    @classmethod
    def confirmed(cls, value):
        if value is not True:
            raise ValueError("Explicit confirmation required")
        return value


class ReviewPolicy(BaseModel):
    version: str
    config_revision: str
    source_version: str
    confidence: float
    margin: float
    enabled: bool
    provider: str
    model: str
    execution_location: str


class MatchPacket(BaseModel):
    product: MatchItemEvidence
    requested: MatchItemEvidence
    recipe_id: UUID
    ingredient_id: UUID
    recipe_name: str
    recipe_instructions: str | None
    recipe_source_fingerprint: str
    recipe_ingredient_line: str
    quantity: str
    unit: str
    facts: EvidenceFacts
    resolved: EvidenceFacts
    fact_sources: dict[
        str,
        Literal[
            "user_provided", "saved_user_provided", "catalog_name", "catalog_ingredients", "recipe_context", "unknown"
        ],
    ]


class MatchPrediction(BaseModel):
    purpose: Literal["identification", "recipe_matching", "adjustment"]
    choice: str
    label: str
    probability: float
    provider_confidence: float
    margin: float
    clears_threshold: bool


class NextClarification(BaseModel):
    field: Literal["identity", "form", "preparation"]
    question: str
    reason_code: Literal["missing_identity_evidence"] = "missing_identity_evidence"


class MatchReviewRead(BaseModel):
    id: UUID
    recipe_id: UUID
    ingredient_id: UUID
    candidate_item_id: UUID
    revision: int
    scope_revision: int
    status: Literal["pending", "evaluating", "confirmed", "rejected", "interrupted"]
    evidence_hash: str
    expires_at: datetime
    packet: MatchPacket
    missing_fields: list[str]
    next_clarification: NextClarification | None = None
    reason_codes: list[str]
    can_confirm: bool
    can_compare: bool
    readiness: Literal[
        "needs_information", "manual_review", "possible_match", "not_recommended", "confirmed", "interrupted"
    ]
    stale: bool
    prediction_stale: bool
    confirmation_required: bool = True
    predictions: list[MatchPrediction]
    policy: ReviewPolicy
    provider_requests: int
    max_provider_requests: int = 2
    selected_source_item_id: UUID | None = None
    confirmed_at: datetime | None = None
    current_choice: bool = False
    confirmation_token: str | None = None


class MatchCandidateList(BaseModel):
    recipe_id: UUID
    ingredient_id: UUID
    requested: MatchItemEvidence
    candidates: list[MatchCandidate]
    requires_admin_review: bool = True


class MatchReviewList(BaseModel):
    reviews: list[MatchReviewRead]
