"""Local AI providers (ADR 0018 §9): free, user-configured, no entitlement.

``ollama`` (a local model server) and ``openai_compat`` (any OpenAI-style
endpoint: OpenAI, OpenRouter, LM Studio, llama.cpp server). Both are
configured through the encrypted connector-config store (ADR 0009) and stay
dormant — no HTTP — until configured.
"""

from __future__ import annotations

import base64
import json
import logging
import re

import httpx
from sqlalchemy.orm import Session

from modules.automation.providers.base import (
    CAP_VISION,
    MAX_IMAGE_BYTES,
    RECEIPT_PROMPT,
    AIUnavailable,
    ReceiptExtraction,
    parse_lines_payload,
)

logger = logging.getLogger(__name__)

_TIMEOUT = 60.0
_JSON_RE = re.compile(r"\{.*\}", re.S)


def _config(db: Session, key: str) -> dict:
    from modules.shopping.services.connector_config import get_effective

    try:
        return get_effective(db, key)
    except Exception:  # noqa: BLE001 - unconfigured/undecryptable = dormant
        return {}


def _extract_json(text: str) -> dict:
    match = _JSON_RE.search(text or "")
    if not match:
        raise AIUnavailable("The model did not return JSON.")
    try:
        return json.loads(match.group(0))
    except json.JSONDecodeError as exc:
        raise AIUnavailable(f"The model returned malformed JSON: {exc}") from exc


def _check_size(image: bytes) -> None:
    if len(image) > MAX_IMAGE_BYTES:
        raise AIUnavailable("Image is too large for AI processing (8 MB max).")


class OllamaProvider:
    key = "ollama"
    label = "Ollama (local)"

    def capabilities(self) -> set[str]:
        return {CAP_VISION}

    def configured(self, db: Session) -> bool:
        cfg = _config(db, "ollama")
        return bool(cfg.get("base_url") and cfg.get("model"))

    def extract_receipt(self, db: Session, image: bytes, *, media_type: str) -> ReceiptExtraction:
        _check_size(image)
        cfg = _config(db, "ollama")
        if not (cfg.get("base_url") and cfg.get("model")):
            raise AIUnavailable("Ollama is not configured.")
        try:
            resp = httpx.post(
                f"{cfg['base_url'].rstrip('/')}/api/chat",
                json={
                    "model": cfg["model"],
                    "stream": False,
                    "format": "json",
                    "messages": [
                        {
                            "role": "user",
                            "content": RECEIPT_PROMPT,
                            "images": [base64.b64encode(image).decode()],
                        }
                    ],
                },
                timeout=_TIMEOUT,
            )
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            raise AIUnavailable(f"Ollama request failed: {exc}") from exc
        content = (resp.json().get("message") or {}).get("content", "")
        return parse_lines_payload(self.key, _extract_json(content))


class OpenAICompatProvider:
    key = "openai_compat"
    label = "OpenAI-compatible API"

    def capabilities(self) -> set[str]:
        return {CAP_VISION}

    def configured(self, db: Session) -> bool:
        cfg = _config(db, "openai_compat")
        return bool(cfg.get("base_url") and cfg.get("api_key") and cfg.get("model"))

    def extract_receipt(self, db: Session, image: bytes, *, media_type: str) -> ReceiptExtraction:
        _check_size(image)
        cfg = _config(db, "openai_compat")
        if not (cfg.get("base_url") and cfg.get("api_key") and cfg.get("model")):
            raise AIUnavailable("The OpenAI-compatible provider is not configured.")
        data_uri = f"data:{media_type};base64,{base64.b64encode(image).decode()}"
        try:
            resp = httpx.post(
                f"{cfg['base_url'].rstrip('/')}/v1/chat/completions",
                headers={"Authorization": f"Bearer {cfg['api_key']}"},
                json={
                    "model": cfg["model"],
                    "response_format": {"type": "json_object"},
                    "messages": [
                        {
                            "role": "user",
                            "content": [
                                {"type": "text", "text": RECEIPT_PROMPT},
                                {"type": "image_url", "image_url": {"url": data_uri}},
                            ],
                        }
                    ],
                },
                timeout=_TIMEOUT,
            )
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            raise AIUnavailable(f"AI provider request failed: {exc}") from exc
        choices = resp.json().get("choices") or [{}]
        content = (choices[0].get("message") or {}).get("content", "")
        return parse_lines_payload(self.key, _extract_json(content))
