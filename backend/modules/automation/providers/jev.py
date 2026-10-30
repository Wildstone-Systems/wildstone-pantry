"""OpenRouter native Decisions API; pinned Jev, no chat or alternate model fallback.

https://openrouter.ai/docs/api/api-reference/alphadecisions/submit-a-decisions-request
Contract verified 2026-10-25. No credentials or provider error text are retained.
"""

from __future__ import annotations

import json
import re
from datetime import datetime

import anyio
import httpx

from modules.automation.classifier_contract import ClassifierConfig, ClassifierUsage, EvaluationRequest
from modules.automation.providers.classifier_common import ClassifierFailure, validate_answers

TIMEOUT_SECONDS = 8.0
MAX_RESPONSE_BYTES = 64 * 1024


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=httpx.Timeout(TIMEOUT_SECONDS), follow_redirects=False, trust_env=False)


def validate_response(payload: dict, request: EvaluationRequest, config: ClassifierConfig) -> dict:
    if not isinstance(payload, dict):
        raise ValueError("response")
    model = payload.get("model")
    if model != config.model:
        match = re.fullmatch(re.escape(config.model) + r"-([0-9]{8})", model) if isinstance(model, str) else None
        if not match:
            raise ValueError("model")
        datetime.strptime(match.group(1), "%Y%m%d")
    raw_usage = payload.get("usage")
    usage = None
    if raw_usage is not None:
        if not isinstance(raw_usage, dict):
            raise ValueError("usage")
        fields = {name: raw_usage[name] for name in ("input_tokens", "output_tokens") if name in raw_usage}
        if "cost" in raw_usage:
            fields["cost_usd"] = raw_usage["cost"]
        if any(value is None for value in fields.values()):
            raise ValueError("usage")
        usage = ClassifierUsage.model_validate(fields).model_dump(exclude_none=True) or None
    return {"answers": validate_answers(payload, request), "resolved_model": model, "usage": usage}


async def _evaluate(config: ClassifierConfig, request: EvaluationRequest) -> dict:
    headers = {"Authorization": f"Bearer {config.api_key}"} if config.execution_location == "cloud_direct" else {}
    payload = {
        "model": config.model,
        "state": request.state,
        "questions": {key: question.model_dump(exclude_none=True) for key, question in request.questions.items()},
        "provider": {"allow_fallbacks": False},
    }
    try:
        # Cancellation covers DNS/connect/headers and the entire streamed body,
        # including a server that continually sends bytes before inactivity timeout.
        # Cancellation closes transport; no background HTTP worker is spawned.
        with anyio.fail_after(TIMEOUT_SECONDS):
            async with _client() as client:
                async with client.stream(
                    "POST", config.endpoint.rstrip("/") + "/api/alpha/decisions", headers=headers, json=payload
                ) as response:
                    code = response.status_code
                    if code != 200:
                        reason = {
                            401: "authentication_failed",
                            403: "authentication_failed",
                            402: "billing_required",
                            429: "rate_limited",
                            413: "provider_input_limit",
                            524: "timeout",
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
