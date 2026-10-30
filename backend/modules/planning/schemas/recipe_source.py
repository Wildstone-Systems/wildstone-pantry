"""Shared typed source annotation contract."""

from typing import Literal

from pydantic import BaseModel, Field


class SourceLine(BaseModel):
    source_line_id: str = Field(max_length=64)
    raw_line: str = Field(max_length=64000)
    line_index: int | None = Field(default=None, ge=0, le=199)
    start: int | None = Field(default=None, ge=0, le=64000)
    end: int | None = Field(default=None, ge=0, le=64000)
    field_path: str | None = Field(default=None, max_length=64)
    role: Literal["ingredient", "instruction", "heading", "compound", "uncertain"]
    warnings: list[str] = Field(max_length=20)
    warning_codes: list[str] = Field(default_factory=list, max_length=20)
