"""Classifier v1 contracts. Shadow decisions have no authority to mutate food data."""

from __future__ import annotations

import ipaddress
import json
import re
from datetime import datetime
from typing import Annotated, Literal
from urllib.parse import urlsplit
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StrictBool, field_validator, model_validator

CONTRACT_VERSION = "classifier.v1"
POLICY_VERSION = "shadow.v1"
SOURCE_VERSION = "ollama.systemone.2026-10-25"

_LAN_NETWORKS = tuple(ipaddress.ip_network(value) for value in (
    "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "fc00::/7",
))
_HOST_LABEL = re.compile(r"(?!-)[A-Za-z0-9-]{1,63}(?<!-)")


def _ollama_server_host(host: str | None) -> bool:
    """Private IP or admin-selected DNS name; never resolve DNS on config reads."""
    if not host or "%" in host:
        return False
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        name = host.removesuffix(".")
        # Reject numeric/legacy IP notation instead of treating it as DNS.
        if not any(char.isalpha() for char in name) or re.fullmatch(
            r"(?:0[xX][0-9a-fA-F]+|[0-9]+)(?:\.(?:0[xX][0-9a-fA-F]+|[0-9]+))*", name
        ):
            return False
        return len(name) <= 253 and all(_HOST_LABEL.fullmatch(label) for label in name.split("."))
    if address.version == 6 and address.ipv4_mapped is not None:
        address = address.ipv4_mapped
    return address.is_loopback or any(
        address.version == network.version and address in network for network in _LAN_NETWORKS
    )


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ClassifierConfig(StrictModel):
    enabled: StrictBool = False
    provider: Literal["ollama", "openrouter"] = "ollama"
    model: str = "clef"
    execution_location: Literal["cloud_direct", "cloud_via_local", "local"] = "cloud_direct"
    endpoint: str = "https://ollama.com"
    cloud_opt_in: StrictBool = False
    runtime_review_enabled: StrictBool = False
    review_confidence: Annotated[float, Field(strict=True, ge=0, le=1, allow_inf_nan=False)] = 0.90
    api_key: str = Field(default="", max_length=4096, exclude=True, repr=False)

    @model_validator(mode="after")
    def endpoint_and_model(self):
        try:
            url = urlsplit(self.endpoint)
            port = url.port
        except ValueError:
            raise ValueError("Invalid classifier endpoint") from None
        if (url.username is not None or url.password is not None or url.query or url.fragment
                or url.path not in ("", "/") or "?" in self.endpoint or "#" in self.endpoint
                or any(char.isspace() or ord(char) < 32 or ord(char) == 127 for char in self.endpoint)):
            raise ValueError("Use an endpoint origin without credentials, path, query or fragment")
        if self.provider == "openrouter":
            if self.execution_location != "cloud_direct":
                raise ValueError("OpenRouter requires direct cloud execution")
            if url.scheme != "https" or url.hostname != "openrouter.ai" or port not in (None, 443):
                raise ValueError("OpenRouter requires https://openrouter.ai")
            allowed = {"typesafe/jev-1.14"}
        elif self.execution_location == "cloud_direct":
            if url.scheme != "https" or url.hostname != "ollama.com" or port not in (None, 443):
                raise ValueError("Direct cloud requires https://ollama.com")
            allowed = {"clef"}
        else:
            if url.scheme not in {"http", "https"} or not _ollama_server_host(url.hostname) or port == 0:
                raise ValueError("Use an HTTP/HTTPS Ollama server origin with a loopback/LAN IP or hostname")
            allowed = {"clef:cloud"} if self.execution_location == "cloud_via_local" else {"clef", "clef:latest"}
        if self.model not in allowed:
            raise ValueError("Model does not match the selected execution location")
        if self.api_key and self.execution_location != "cloud_direct":
            raise ValueError("API keys are only stored for direct Ollama Cloud")
        return self


class ConfigUpdate(StrictModel):
    enabled: StrictBool | None = None
    provider: Literal["ollama", "openrouter"] | None = None
    model: str | None = None
    execution_location: Literal["cloud_direct", "cloud_via_local", "local"] | None = None
    endpoint: str | None = None
    cloud_opt_in: StrictBool | None = None
    runtime_review_enabled: StrictBool | None = None
    review_confidence: Annotated[float, Field(strict=True, ge=0, le=1, allow_inf_nan=False)] | None = None
    api_key: str | None = Field(default=None, max_length=4096, repr=False)

    @model_validator(mode="before")
    @classmethod
    def no_nulls(cls, value):
        if isinstance(value, dict) and any(v is None for v in value.values()):
            raise ValueError("Omit fields to keep their saved values; null is not supported")
        return value


def decode_config(values: dict[str, str]) -> ClassifierConfig:
    data: dict = dict(values)
    for key in ("enabled", "cloud_opt_in", "runtime_review_enabled"):
        if key in data:
            if data[key] not in ("true", "false"):
                raise ValueError("Invalid saved classifier boolean")
            data[key] = data[key] == "true"
    if "review_confidence" in data:
        data["review_confidence"] = float(data["review_confidence"])
    return ClassifierConfig.model_validate(data)


class Question(StrictModel):
    type: Literal["choice", "noul", "score"]
    instructions: str = Field(min_length=1, max_length=1000)
    criteria: dict[str, str | None] | list[str] | None = None

    @model_validator(mode="after")
    def typed_criteria(self):
        criteria = self.criteria
        if self.type == "choice":
            if not isinstance(criteria, dict) or not 2 <= len(criteria) <= 26:
                raise ValueError("Choice questions require 2–26 candidates")
            if any(not re.fullmatch(r"c_[0-9]{1,3}", key) for key in criteria):
                raise ValueError("Use opaque candidate IDs such as c_1")
        elif self.type == "score":
            if not isinstance(criteria, list) or not 2 <= len(criteria) <= 10:
                raise ValueError("Score questions require 2–10 ordered descriptions")
        elif criteria is not None and (not isinstance(criteria, dict) or set(criteria) != {"true", "false"}):
            raise ValueError("Yes/no criteria must describe true and false")
        if criteria is not None and any(
            v is not None and (not v.strip() or len(v) > 500)
            for v in (criteria.values() if isinstance(criteria, dict) else criteria)
        ):
            raise ValueError("Criteria descriptions must be 1–500 characters")
        return self


class EvaluationRequest(StrictModel):
    contract_version: Literal["classifier.v1"] = CONTRACT_VERSION
    policy_version: Literal["shadow.v1"] = POLICY_VERSION
    source_version: Literal["manual.v1", "connection-test.v1"] = "manual.v1"
    state: str = Field(min_length=1, max_length=8000)
    questions: dict[str, Question] = Field(min_length=1, max_length=8)

    @field_validator("state")
    @classmethod
    def nonempty(cls, value):
        if not value.strip():
            raise ValueError("State cannot be blank")
        return value

    @model_validator(mode="after")
    def bounded_request(self):
        if any(not re.fullmatch(r"q_[0-9]{1,3}", key) for key in self.questions):
            raise ValueError("Use opaque question IDs such as q_1")
        if len(json.dumps(self.model_dump(), ensure_ascii=False).encode()) > 32 * 1024:
            raise ValueError("Classifier input and schema must fit within 32 KiB")
        return self


class Answer(StrictModel):
    type: Literal["choice", "noul", "score"]
    choice: str | None = None
    probabilities: dict[str, Annotated[float, Field(strict=True, ge=0, le=1, allow_inf_nan=False)]] | None = None
    confidence: Annotated[float, Field(strict=True, ge=0, le=1, allow_inf_nan=False)] | None = None
    noul: Annotated[float, Field(strict=True, ge=0, le=1, allow_inf_nan=False)] | None = None
    score: Annotated[float, Field(strict=True, ge=0, le=9, allow_inf_nan=False)] | None = None


class ConfigView(StrictModel):
    config: ClassifierConfig
    api_key_set: bool
    configured: bool
    secrets_available: bool
    valid: bool
    detail: str


class ClassifierUsage(StrictModel):
    input_tokens: Annotated[int, Field(strict=True, ge=0, le=9007199254740991)] | None = None
    output_tokens: Annotated[int, Field(strict=True, ge=0, le=9007199254740991)] | None = None
    cost_usd: Annotated[float, Field(strict=True, ge=0, allow_inf_nan=False)] | None = None


class EvaluationResult(StrictModel):
    id: UUID
    status: Literal["ok", "abstain", "unavailable", "invalid"]
    reason_code: str
    answers: dict[str, Answer]
    provider: Literal["ollama", "openrouter"]
    model: str
    execution_location: Literal["cloud_direct", "cloud_via_local", "local"]
    contract_version: Literal["classifier.v1"]
    policy_version: Literal["shadow.v1"]
    source_version: Literal["manual.v1", "connection-test.v1"]
    provider_contract_version: str
    resolved_model: str | None = None
    usage: ClassifierUsage | None = None
    created_at: datetime
    expires_at: datetime
