"""Public read-only culinary evidence; no ORM or numeric-transfer authority."""

from typing import Literal
from uuid import UUID

from pydantic import BaseModel

from modules.catalog.schemas.product_evidence import ProductEvidenceRead


class MatchItemEvidence(BaseModel):
    item_id: UUID
    name: str
    brand: str | None
    ingredients_text: str | None
    source: str
    canonical_ingredient: str | None
    canonical_provenance: str
    canonical_version: int | None
    canonical_revision: int
    nutrition_basis: Literal["estimated", "label_or_manual", "unknown"]
    nutrition_source: str | None
    nutrition_estimated: bool
    nutrition_values: dict[str, str | None]
    source_fingerprint: str
    base_source_fingerprint: str | None = None
    structured_evidence: ProductEvidenceRead | None = None
    identity_evidence_truncated: bool = False


class MatchCandidate(BaseModel):
    item: MatchItemEvidence
    retrieval_reason: Literal["same_item", "confirmed_identity", "exact_name", "token_overlap"]
