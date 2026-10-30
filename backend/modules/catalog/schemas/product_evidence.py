"""Explicit administrator facts; no inferred verification or unknown placeholders."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import Field, StrictBool, field_validator

from modules.automation.classifier_contract import StrictModel


class ProductFacts(StrictModel):
    identity: str | None = Field(default=None, max_length=200)
    base_ingredient: str | None = Field(default=None, max_length=200)
    form: str | None = Field(default=None, max_length=200)
    preparation: str | None = Field(default=None, max_length=200)
    composition: str | None = Field(default=None, max_length=1000)

    @field_validator("*")
    @classmethod
    def normalize_unknown(cls, value):
        if value is None:
            return None
        value = value.strip()
        return None if value.lower() in {"", "unknown", "?", "n/a", "none", "not sure", "unsure"} else value


class SaveProductEvidence(StrictModel):
    confirm: StrictBool
    expected_revision: int = Field(strict=True, ge=0)
    base_source_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    facts: ProductFacts

    @field_validator("confirm")
    @classmethod
    def explicit_save(cls, value):
        if value is not True:
            raise ValueError("Explicit product facts save required")
        return value


class ProductEvidenceRead(StrictModel):
    item_id: UUID
    revision: int
    base_source_hash: str
    saved_base_source_hash: str | None
    facts: ProductFacts
    stale: bool
    updated_by: UUID | None
    updated_at: datetime | None
    provenance: Literal["user_provided"] = "user_provided"
