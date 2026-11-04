"""JSON-safe 422 responses for request-validation errors.

FastAPI's default handler echoes the rejected input back in the error body.
Python's JSON parser accepts the non-standard bare literals ``NaN``,
``Infinity`` and ``-Infinity`` and turns them into floats, which the response
encoder then refuses (``allow_nan=False``) — so a client that sent one got a
dropped connection / 500 instead of the 422 it deserved, on every endpoint with
a numeric field. This handler keeps FastAPI's exact response shape and only
renders non-finite floats as the strings ``"nan"`` / ``"inf"`` / ``"-inf"``.
"""

from __future__ import annotations

import math
from typing import Any

from fastapi import Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse


def _finite_safe(value: Any) -> Any:
    if isinstance(value, float) and not math.isfinite(value):
        return str(value)
    if isinstance(value, dict):
        return {key: _finite_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_finite_safe(item) for item in value]
    return value


async def request_validation_error_handler(_request: Request, exc: RequestValidationError) -> JSONResponse:
    return JSONResponse(status_code=422, content={"detail": _finite_safe(jsonable_encoder(exc.errors()))})
