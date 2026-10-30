"""Ollama System One adapter, pinned to the documented 2026-10-25 contract.

Official references: https://docs.ollama.com/api/systemone and
https://ollama.com/library/clef. No chat/generation or provider fallback.
"""

from __future__ import annotations

import json

import anyio
import httpx

from modules.automation.classifier_contract import ClassifierConfig, EvaluationRequest
from modules.automation.providers.classifier_common import ClassifierFailure, validate_answers

TIMEOUT_SECONDS = 8.0
MAX_RESPONSE_BYTES = 64 * 1024


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=httpx.Timeout(TIMEOUT_SECONDS), follow_redirects=False, trust_env=False)


def validate_response(payload: dict, request: EvaluationRequest, config: ClassifierConfig) -> dict:
    if not isinstance(payload, dict) or payload.get("model") not in {config.model, "clef"}:
        raise ValueError("model")
    return validate_answers(payload, request)


async def _evaluate(config: ClassifierConfig, request: EvaluationRequest) -> dict:
    headers = {"Authorization": f"Bearer {config.api_key}"} if config.execution_location == "cloud_direct" else {}
    payload = {
        "model": config.model,
        "state": request.state,
        "questions": {key: question.model_dump(exclude_none=True) for key, question in request.questions.items()},
    }
    try:
        # Cancellation covers DNS/connect/headers and the entire streamed body,
        # including a server that continually sends bytes before inactivity timeout.
        # Cancellation closes transport; no background HTTP worker is spawned.
        with anyio.fail_after(TIMEOUT_SECONDS):
            async with _client() as client:
                async with client.stream(
                    "POST", config.endpoint.rstrip("/") + "/v1/systemone", headers=headers, json=payload
                ) as response:
                    code = response.status_code
                    if code != 200:
                        reason = {
                            401: "authentication_failed",
                            403: "authentication_failed",
                            402: "billing_required",
                            429: "rate_limited",
                            413: "provider_input_limit",
                        }.get(code, "redirect_refused" if 300 <= code < 400 else "provider_error")
                        raise ClassifierFailure("unavailable", reason)
                    data = bytearray()
                    async for chunk in response.aiter_bytes():
                        data.extend(chunk)
                        if len(data) > MAX_RESPONSE_BYTES:
                            raise ClassifierFailure("invalid", "response_too_large")
        decoded = json.loads(data)
        return validate_response(decoded, request, config)
    except ClassifierFailure:
        raise
    except (httpx.TimeoutException, TimeoutError):
        raise ClassifierFailure("unavailable", "timeout") from None
    except httpx.HTTPError:
        raise ClassifierFailure("unavailable", "connection_failed") from None
    except (ValueError, TypeError, KeyError, RecursionError, OverflowError):
        raise ClassifierFailure("invalid", "invalid_provider_response") from None


def evaluate(config: ClassifierConfig, request: EvaluationRequest) -> dict:
    """Sync service seam; routes run in FastAPI's worker, with cancellable I/O."""
    return anyio.run(_evaluate, config, request)


async def evaluate_async(config: ClassifierConfig, request: EvaluationRequest) -> dict:
    """Request-owned benchmark seam; cancellation propagates to the transport."""
    return await _evaluate(config, request)
