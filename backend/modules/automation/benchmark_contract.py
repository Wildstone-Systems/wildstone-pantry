"""Closed benchmark commands: checked-in synthetic evidence only."""

from typing import Annotated, Literal

from pydantic import Field, StrictBool, field_validator

from modules.automation.classifier_contract import StrictModel

CASE_LIMIT = 500
PREVIOUS_CORPUS_HASH = "4ed5cae8fe5e377be0da8d49de5a23093de77dced0fd746ff37b6c3141d691a6"
DIAGNOSTIC_TASKS = ("nutrition_transfer", "density_transfer")
DIAGNOSTIC_SPLITS = {"train": (140, 1200), "holdout": (66, 600)}
DIAGNOSTIC_POLICY = dict(
    confidence=0.90,
    margin=0.20,
    min_support=20,
    min_groups=5,
    max_false_positives=0,
    max_p95_ms=5000,
    max_failure_rate=0.05,
    budget_unit="requests",
    max_requests=140,
)


class UpgradeCommand(StrictModel):
    previous_corpus_hash: Literal["4ed5cae8fe5e377be0da8d49de5a23093de77dced0fd746ff37b6c3141d691a6"]
    confirm: StrictBool

    @field_validator("confirm")
    @classmethod
    def confirmed(cls, value):
        if not value:
            raise ValueError("Explicit draft upgrade confirmation required")
        return value


class ReviewCommand(StrictModel):
    corpus_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    review_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    case_id: str = Field(max_length=80)
    label: str = Field(max_length=40)
    confirm: StrictBool

    @field_validator("confirm")
    @classmethod
    def confirmed(cls, value):
        if not value:
            raise ValueError("Explicit confirmation required")
        return value


class FreezeCommand(StrictModel):
    review_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    confirm: StrictBool

    @field_validator("confirm")
    @classmethod
    def confirmed(cls, value):
        if not value:
            raise ValueError("Explicit confirmation required")
        return value


class TaskPolicy(StrictModel):
    confidence: Annotated[float, Field(strict=True, ge=0, le=1, allow_inf_nan=False)]
    margin: Annotated[float, Field(strict=True, ge=0, le=1, allow_inf_nan=False)]
    min_support: int = Field(strict=True, ge=1, le=CASE_LIMIT)
    min_groups: int = Field(strict=True, ge=2, le=CASE_LIMIT)
    max_false_positives: int = Field(strict=True, ge=0, le=0)
    max_p95_ms: int = Field(strict=True, ge=1, le=8000)
    max_failure_rate: Annotated[float, Field(strict=True, ge=0, le=1, allow_inf_nan=False)]
    budget_unit: Literal["requests"]
    max_requests: int = Field(strict=True, ge=1, le=CASE_LIMIT)


class PolicyCommand(StrictModel):
    review_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    tasks: dict[str, TaskPolicy] = Field(min_length=1, max_length=7)


class RunCommand(StrictModel):
    engine: Literal["rules", "classifier", "legacy_judge"]
    split: Literal["train", "holdout"]
    max_requests: int = Field(strict=True, ge=1, le=CASE_LIMIT)
    max_seconds: int = Field(strict=True, ge=1, le=1800)
    budget_confirmed: StrictBool
