"""Strict bounded global ingredient identity review, separate from recipe choices."""

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, field_validator

from modules.catalog.schemas.product_evidence import ProductFacts


class StrictDTO(BaseModel):
    model_config = ConfigDict(extra="forbid")


class EvaluateIngredientMatches(StrictDTO):
    recipe_id: UUID
    ingredient_id: UUID


class RankedReviewBinding(StrictDTO):
    revision: Annotated[StrictInt, Field(ge=0)]
    evidence_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    confirmation_token: str = Field(min_length=20, max_length=200)


class ConfirmRankedAlias(RankedReviewBinding):
    candidate_item_id: UUID
    confirm_global_alias: StrictBool
    replace_confirmed: StrictBool = False

    @field_validator("confirm_global_alias")
    @classmethod
    def explicit_true(cls, value):
        if value is not True:
            raise ValueError("Explicit global alias confirmation required")
        return value


class RankedIdentityEvidence(StrictDTO):
    brand: str | None
    ingredients_text: str | None
    source: str
    current_facts: ProductFacts
    facts_provenance: Literal["saved_user_provided", "unknown"]
    saved_facts_stale: bool
    truncated: bool
    nutrition_basis: Literal["estimated", "label_or_manual", "unknown"]
    nutrition_source: str | None


class RankedCandidate(StrictDTO):
    candidate_item_id: UUID
    product_name: str
    requested_name: str
    product_evidence: RankedIdentityEvidence
    canonical_ingredient: str | None
    canonical_provenance: str
    canonical_version: int | None
    canonical_revision: int
    retrieval_reason: str
    label: Literal["same_ingredient", "possible_substitution", "different_ingredient", "insufficient_evidence"]
    decision_provenance: Literal["same_item", "confirmed_identity", "deterministic_guard", "classifier", "abstention"]
    probability: float | None = None
    provider_confidence: float | None = None
    margin: float | None = None
    clears_threshold: bool = False
    reason_codes: list[str] = Field(default_factory=list, max_length=12)
    can_confirm: bool = False


class RankedReviewRead(StrictDTO):
    id: UUID
    recipe_id: UUID
    ingredient_id: UUID
    requested_name: str
    requested_evidence: RankedIdentityEvidence
    requested_canonical_ingredient: str | None
    revision: int
    status: Literal["evaluating", "pending", "confirmed", "rejected", "interrupted"]
    evidence_hash: str
    expires_at: datetime
    confirmation_token: str | None = None
    results: list[RankedCandidate] = Field(max_length=3)
    reason_codes: list[str]
    stale: bool
    ambiguous_tie: bool
    provider_requests: int
    max_provider_requests: Literal[3] = 3
    policy: dict
    confirmation_required: Literal[True] = True
    alias_scope: Literal["permanent_household_product_identity"] = "permanent_household_product_identity"
