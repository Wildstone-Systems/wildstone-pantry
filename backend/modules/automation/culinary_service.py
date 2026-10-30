"""Public request-owned culinary advice; no database writes or truth authority."""

import hashlib
from pathlib import Path

import anyio

from app.core.crypto import SecretDecryptError, SecretStorageUnavailable
from app.db.session import SessionLocal
from modules.automation.classifier_contract import ClassifierConfig, decode_config
from modules.automation.providers import clef, jev
from modules.automation.providers.classifier_common import ClassifierFailure
from modules.shopping.services.connector_config import classifier_revision, classifier_snapshot


def source_version():
    root = Path(__file__).parent
    files = [
        Path(__file__),
        root / "culinary_questions.py",
        root / "classifier_contract.py",
        root / "providers/clef.py",
        root / "providers/jev.py",
        root / "providers/classifier_common.py",
        root.parent / "planning/services/ingredient_match_reviews.py",
        root.parent / "planning/schemas/ingredient_match_review.py",
        root.parent / "catalog/services/match_evidence.py",
        root.parent / "catalog/schemas/match_evidence.py",
        root.parent / "catalog/services/product_evidence.py",
        root.parent / "catalog/schemas/product_evidence.py",
        root.parent / "catalog/product_evidence_repository.py",
        root.parent / "catalog/models/product_evidence.py",
        root.parent / "catalog/match_evidence_repository.py",
        root / "ingredient_identity_questions.py",
        root.parent / "catalog/services/ranked_match_candidates.py",
        root.parent / "catalog/ranked_match_repository.py",
        root.parent / "catalog/services/items.py",
        root.parent / "catalog/services/identity.py",
        root.parent / "planning/services/recipes.py",
        root.parent / "planning/services/ranked_ingredient_reviews.py",
        root.parent / "planning/schemas/ranked_ingredient_review.py",
        root.parent / "planning/api/ranked_ingredient_reviews.py",
        root.parent / "planning/ranked_ingredient_repository.py",
        root.parent / "planning/models/ranked_ingredient_review.py",
        root.parent / "inventory/services/review_snapshot.py",
        root.parent / "inventory/review_snapshot_repository.py",
    ]
    return hashlib.sha256(b"".join(path.read_bytes() for path in files)).hexdigest()


def review_policy(db, *, lock=False):
    try:
        snapshot = classifier_snapshot(db, lock=lock)
        config = decode_config(snapshot["values"])
        revision = snapshot["revision"]
        configured = bool(config.api_key) if config.execution_location == "cloud_direct" else True
    except (ValueError, SecretDecryptError, SecretStorageUnavailable):
        config, revision, configured = ClassifierConfig(), classifier_revision(db), False
    return _policy(config, revision, configured)


def _policy(config, revision, configured):
    config = config.model_dump()
    return {
        "version": "culinary-review.v1",
        "config_revision": revision,
        "source_version": source_version(),
        "confidence": config["review_confidence"],
        "margin": 0.20,
        "enabled": bool(
            configured
            and config["enabled"]
            and config["runtime_review_enabled"]
            and (config["execution_location"] == "local" or config["cloud_opt_in"])
        ),
        "provider": config["provider"],
        "model": config["model"],
        "execution_location": config["execution_location"],
    }


async def evaluate_culinary(db, request, *, expected_policy, stop_requested):
    """One eight-second adapter invocation; configuration captured and rechecked."""
    try:
        snapshot = classifier_snapshot(db)
        config = decode_config(snapshot["values"])
    except (ValueError, SecretDecryptError, SecretStorageUnavailable):
        raise ClassifierFailure("unavailable", "configuration_unavailable") from None
    configured = bool(config.api_key) if config.execution_location == "cloud_direct" else True
    if _policy(config, snapshot["revision"], configured) != expected_policy or not expected_policy["enabled"]:
        raise ClassifierFailure("unavailable", "review_disabled_or_changed")
    # Reservation may have committed; use an independent read immediately before
    # I/O, then keep the exact in-memory approved configuration for dispatch.
    with SessionLocal() as fresh:
        if review_policy(fresh) != expected_policy:
            raise ClassifierFailure("unavailable", "configuration_changed")
    if await stop_requested():
        raise ClassifierFailure("unavailable", "cancelled_or_changed")
    result = None
    cancelled = False

    async def watch(scope):
        nonlocal cancelled
        while True:
            await anyio.sleep(0.1)
            with SessionLocal() as fresh:
                changed = review_policy(fresh) != expected_policy
            if changed or await stop_requested():
                cancelled = True
                scope.cancel()
                return

    with anyio.move_on_after(8) as deadline:
        async with anyio.create_task_group() as group:
            group.start_soon(watch, deadline)
            try:
                result = await (
                    clef.evaluate_async(config, request)
                    if config.provider == "ollama"
                    else jev.evaluate_async(config, request)
                )
            except ClassifierFailure as exc:
                # Handle inside task group so callers receive a sanitized ordinary
                # failure rather than an ExceptionGroup containing transport state.
                result = exc
            group.cancel_scope.cancel()
    if cancelled:
        raise ClassifierFailure("unavailable", "cancelled_or_changed")
    if deadline.cancel_called:
        raise ClassifierFailure("unavailable", "timeout")
    if isinstance(result, ClassifierFailure):
        raise result
    return result if config.provider == "ollama" else result["answers"]
