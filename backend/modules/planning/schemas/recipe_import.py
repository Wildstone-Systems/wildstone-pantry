from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel, Field

from modules.planning.schemas.recipe import FlaggedUnit, RecipeIngredientSpec
from modules.planning.schemas.recipe_source import SourceLine


class RecipeParsedIngredient(RecipeIngredientSpec):
    raw_line: str
    item_id: UUID | None = None
    resolution: str | None = None
    unit_resolution: str | None = None
    suggestion: str | None = None


class RecipeParseTextRequest(BaseModel):
    raw_text: str = Field(..., min_length=1)


class RecipeParseResponse(BaseModel):
    source_lines: list[SourceLine] = Field(default_factory=list)
    title: str
    instructions: str
    ingredients: list[RecipeParsedIngredient]
    raw_text: str


class RecipeIngestParsedRequest(BaseModel):
    title: str = Field(..., min_length=1, max_length=200)
    instructions: str | None = None
    ingredients: list[RecipeParsedIngredient] = Field(default_factory=list)
    source: str = Field(default="text", max_length=32)
    source_filename: str | None = Field(default=None, max_length=256)
    source_text: str | None = None
    create_missing_items: bool = True


class RecipeIngestParsedResponse(BaseModel):
    recipe_id: UUID
    created_item_ids: list[UUID]
    # Units the parsed source used that aren't in the recipe vocabulary (ADR 0017 §3).
    # The lines are still ingested; these tell the caller what needs remapping.
    flagged_units: list[FlaggedUnit] = Field(default_factory=list)
