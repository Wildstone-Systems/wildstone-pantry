"""Bounded import review contracts; classifiers cannot supply writable values."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from modules.planning.schemas.recipe import RecipeCreate
from modules.planning.schemas.recipe_source import SourceLine


class ImportSource(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["text", "document", "structured"]
    raw_text: str | None = Field(default=None, max_length=64000)
    structured: RecipeCreate | None = None
    filename: str | None = Field(default=None, max_length=256)

    @model_validator(mode="after")
    def complete(self):
        if self.kind == "structured":
            if self.structured is None or self.raw_text is not None:
                raise ValueError("Structured source requires its original recipe snapshot")
        elif not self.raw_text or self.structured is not None:
            raise ValueError("Text/document source requires original raw_text")
        if self.raw_text and len(self.raw_text.splitlines()) > 200:
            raise ValueError("Import review supports at most 200 physical source lines; split the source")
        return self


class ImportReviewInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    recipe: RecipeCreate
    source: ImportSource

    @model_validator(mode="after")
    def bounded(self):
        for recipe in (self.recipe, self.source.structured):
            if recipe and len(recipe.ingredients or []) > 200:
                raise ValueError("Import review supports at most 200 ingredient rows")
        return self


class ReviewedImportInput(ImportReviewInput):
    confirmed: Literal[True]
    reviewed_input_hash: str = Field(min_length=64, max_length=64)


class ImportAnnotation(BaseModel):
    source_line_id: str = Field(max_length=64)
    role: Literal["ingredient", "instruction", "heading", "compound", "uncertain"] | None
    score: float | None = Field(default=None, ge=0, le=1, allow_inf_nan=False)
    below_threshold: bool
    threshold: float = Field(ge=0, le=1, allow_inf_nan=False)
    status: str = Field(max_length=64)


class ImportReviewResponse(BaseModel):
    version: Literal["recipe-import-review.v1"]
    binding: str = Field(min_length=64, max_length=64)
    warning: str = Field(max_length=500)
    lines: list[SourceLine] = Field(max_length=202)
    annotations: list[ImportAnnotation] = Field(default_factory=list, max_length=8)
    status: Literal["rules", "unavailable", "disabled", "stale_configuration", "stale_source", "advisory"]
    provider_requests: int = Field(ge=0, le=8)
