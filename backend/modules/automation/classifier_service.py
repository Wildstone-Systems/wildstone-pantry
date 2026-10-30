"""Admin-only shadow evaluation. Never imported by truth-writing consumers."""

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy.orm import Session

from app.core.crypto import SecretDecryptError, SecretStorageUnavailable, secrets_available
from modules.automation import classifier_repository as repository
from modules.automation.classifier_contract import (
    CONTRACT_VERSION,
    POLICY_VERSION,
    SOURCE_VERSION,
    ClassifierConfig,
    EvaluationRequest,
    decode_config,
)
from modules.automation.providers import clef, jev
from modules.automation.providers.classifier_common import ClassifierFailure
from modules.shopping.services import connector_config

RETENTION_MINUTES = 15


def _config(db: Session) -> ClassifierConfig:
    return decode_config(connector_config.get_classifier_config(db))


def config_view(db: Session) -> dict:
    try:
        config = _config(db)
        valid = True
    except (ValueError, SecretDecryptError, SecretStorageUnavailable):
        config = ClassifierConfig()
        valid = False
    configured = valid and (bool(config.api_key) if config.execution_location == "cloud_direct" else True)
    return {
        "config": config.model_dump(),
        "api_key_set": bool(config.api_key),
        "configured": configured,
        "secrets_available": secrets_available(),
        "valid": valid,
        "detail": "Saved settings are invalid or unreadable. Disconnect and configure again."
        if not valid
        else "Classifier shadow mode; outputs never change pantry data.",
    }


def update_config(db: Session, payload: dict) -> dict:
    connector_config.set_classifier_config(db, payload)
    repository.clear_results(db)
    return config_view(db)


def disconnect(db: Session) -> dict:
    connector_config.clear_config(db, "classifier")
    repository.clear_results(db)
    return config_view(db)


def run_evaluation(db: Session, payload: dict, *, connection_test: bool = False, expected_revision: str | None = None) -> dict:
    request = EvaluationRequest.model_validate(payload)
    now = datetime.now(UTC)
    expires = now + timedelta(minutes=RETENTION_MINUTES)
    result = {
        "id": str(uuid4()),
        "status": "abstain",
        "reason_code": "disabled",
        "answers": {},
        "provider": "ollama",
        "model": "clef",
        "execution_location": "cloud_direct",
        "contract_version": CONTRACT_VERSION,
        "policy_version": POLICY_VERSION,
        "source_version": request.source_version,
        "provider_contract_version": SOURCE_VERSION,
        "created_at": now.isoformat(),
        "expires_at": expires.isoformat(),
    }
    try:
        if expected_revision is not None:
            snapshot = connector_config.classifier_snapshot(db)
            if snapshot["revision"] != expected_revision:
                result["reason_code"] = "stale_configuration"
                repository.save_result(db, result, now, expires)
                return result
            config = decode_config(snapshot["values"])
        else:
            config = _config(db)
        result.update(provider=config.provider, model=config.model, execution_location=config.execution_location)
        if config.provider == "openrouter":
            result["provider_contract_version"] = "openrouter.decisions.2026-10-25"
        if not config.enabled and not connection_test:
            pass
        elif config.execution_location != "local" and not config.cloud_opt_in:
            result["reason_code"] = "cloud_consent_required"
        elif config.execution_location == "cloud_direct" and not config.api_key:
            result.update(status="unavailable", reason_code="not_configured")
        else:
            if config.provider == "openrouter":
                result.update(jev.evaluate(config, request))
            else:
                result["answers"] = clef.evaluate(config, request)
            result.update(status="ok", reason_code="shadow_only")
    except (ValueError, SecretDecryptError, SecretStorageUnavailable):
        result.update(status="unavailable", reason_code="invalid_configuration")
    except ClassifierFailure as exc:
        result.update(status=exc.status, reason_code=exc.code)
    repository.save_result(db, result, now, expires)
    return result


def connection_test(db: Session) -> dict:
    return run_evaluation(
        db,
        {
            "state": "A synthetic test item is an apple.",
            "source_version": "connection-test.v1",
            "questions": {
                "q_1": {
                    "type": "choice",
                    "instructions": "Which category describes the synthetic item?",
                    "criteria": {"c_1": "Food", "c_2": "Other"},
                }
            },
        },
        connection_test=True,
    )


def read_evaluation(db: Session, result_id: UUID) -> dict | None:
    return repository.read_result(db, result_id, datetime.now(UTC))
