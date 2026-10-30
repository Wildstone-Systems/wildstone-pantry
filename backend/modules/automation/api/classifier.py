"""Dedicated classifier API; request validation errors never echo secrets/state."""

import json
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.core.crypto import SecretDecryptError, SecretStorageUnavailable
from app.core.security import require_authenticated_admin
from app.db import get_db
from modules.automation import classifier_service as service
from modules.automation.classifier_contract import ConfigUpdate, ConfigView, EvaluationRequest, EvaluationResult

router = APIRouter(prefix="/ai/classifier", tags=["classifier"], dependencies=[Depends(require_authenticated_admin)])


def _request_body(model) -> dict:
    """Describe safe manually parsed bodies, with inline nested question schemas."""
    schema = model.model_json_schema()
    definitions = schema.pop("$defs", {})

    def inline(value):
        if isinstance(value, list):
            return [inline(item) for item in value]
        if isinstance(value, dict):
            if "$ref" in value:
                return inline(definitions[value["$ref"].rsplit("/", 1)[-1]])
            return {key: inline(item) for key, item in value.items()}
        return value

    schema = inline(schema)
    if model is ConfigUpdate:
        # Omission is supported, but the runtime contract forbids explicit null.
        for field in schema["properties"].values():
            variants = [item for item in field.get("anyOf", []) if item.get("type") != "null"]
            if variants:
                field.pop("anyOf")
                if len(variants) == 1:
                    field.update(variants[0])
                else:
                    field["anyOf"] = variants
            field.pop("default", None)
        schema["properties"]["api_key"].update(writeOnly=True, format="password")
    return {"requestBody": {"required": True, "content": {"application/json": {"schema": schema}}}}


async def _payload(request: Request) -> dict:
    # Framework validation echoes offending input, which may contain an API key.
    # Parse manually and return only a constant error for every malformed shape.
    try:
        data = bytearray()
        async for chunk in request.stream():
            if len(data) + len(chunk) > 40 * 1024:
                raise ValueError
            data.extend(chunk)
        value = json.loads(data)
        if not isinstance(value, dict):
            raise ValueError
        return value
    except (ValueError, UnicodeDecodeError, RecursionError):
        raise HTTPException(400, "Expected a JSON object") from None


@router.get("/config", response_model=ConfigView)
def config(db: Session = Depends(get_db)):
    return service.config_view(db)


@router.put("/config", response_model=ConfigView, openapi_extra=_request_body(ConfigUpdate))
def update(payload: dict = Depends(_payload), db: Session = Depends(get_db)):
    try:
        return service.update_config(db, payload)
    except SecretStorageUnavailable:
        raise HTTPException(409, "Encrypted settings storage is unavailable") from None
    except (ValueError, SecretDecryptError):
        raise HTTPException(400, "Invalid classifier settings; check endpoint, model and execution location") from None


@router.delete("/config", response_model=ConfigView)
def disconnect(db: Session = Depends(get_db)):
    return service.disconnect(db)


@router.post("/test", response_model=EvaluationResult, response_model_exclude_none=True)
def test_connection(db: Session = Depends(get_db)):
    return service.connection_test(db)


@router.post(
    "/evaluations",
    response_model=EvaluationResult,
    response_model_exclude_none=True,
    openapi_extra=_request_body(EvaluationRequest),
)
def evaluate(payload: dict = Depends(_payload), db: Session = Depends(get_db)):
    try:
        return service.run_evaluation(db, payload)
    except ValueError:
        raise HTTPException(400, "Invalid classifier request; check typed questions and input limits") from None


@router.get("/evaluations/{result_id}", response_model=EvaluationResult, response_model_exclude_none=True)
def read(result_id: UUID, db: Session = Depends(get_db)):
    result = service.read_evaluation(db, result_id)
    if result is None:
        raise HTTPException(404, "Classifier result is expired or unavailable")
    return result
