"""Request-owned synthetic evaluation. No background jobs or pantry writes."""

import copy
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path
from uuid import NAMESPACE_URL, uuid4, uuid5

import anyio
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.session import SessionLocal
from modules.automation import benchmark_repository as repository
from modules.automation import classifier_service
from modules.automation.benchmark_contract import (
    CASE_LIMIT,
    DIAGNOSTIC_POLICY,
    DIAGNOSTIC_SPLITS,
    DIAGNOSTIC_TASKS,
    PREVIOUS_CORPUS_HASH,
    FreezeCommand,
    PolicyCommand,
    ReviewCommand,
    RunCommand,
    UpgradeCommand,
)
from modules.automation.benchmark_corpus import LABELS, PROMPT_VERSION, digest, load, request_for
from modules.automation.benchmark_metrics import summarize
from modules.automation.classifier_contract import EvaluationRequest
from modules.automation.providers import clef, jev
from modules.automation.providers.classifier_common import ClassifierFailure
from modules.shopping.services import connector_config

CORPUS = load()
CORPUS_HASH = digest(CORPUS)
CASES = {case["id"]: case for case in CORPUS["cases"]}
# Server-side test seam. External transport harnesses MUST set this; no browser
# command can attest its own transport. This is provenance, not tamper-proofing.
TRANSPORT_FIXTURE = False


def content_fingerprint():
    root = Path(__file__).parent
    paths = list(root.glob("benchmark_*.py")) + [
        root / "providers" / name for name in ("clef.py", "jev.py", "classifier_common.py")
    ]
    paths += [
        root / "classifier_contract.py",
        root / "classifier_service.py",
        root / "food_judge.py",
        root / "receipt_parser.py",
        root / "recipe_import_parser.py",
        root.parent / "catalog" / "services" / "canonical.py",
        root.parent / "catalog" / "services" / "units.py",
        root.parent / "shopping" / "services" / "connector_config.py",
    ]
    return digest(
        {path.relative_to(root.parent).as_posix(): path.read_text(encoding="utf-8") for path in sorted(paths)}
        | {"corpus_hash": CORPUS_HASH}
    )


def _source_revision():
    try:
        return subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=Path(__file__).parents[3],
            capture_output=True,
            text=True,
            timeout=2,
            check=True,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return "unknown"


def _marker_id(corpus_hash):
    return uuid5(NAMESPACE_URL, "pantryos.classifier.holdout." + corpus_hash)


def _draft_document(exposed=False):
    return {
        "corpus_hash": CORPUS_HASH,
        "labels": {key: case["proposed_label"] for key, case in CASES.items()},
        "reviewed": {},
        "frozen": False,
        "policy": None,
        "holdout_exposed": exposed,
    }


def _compatibility(state):
    row, old, new = state["review"], state["old_marker"], state["new_marker"]
    if row is not None and row.document.get("corpus_hash") == CORPUS_HASH:
        return False, False, "The current AI draft version is available."
    if row is None and old is None:
        return False, False, "The current AI draft version is available for a fresh review."
    if row is None or row.kind != "review" or row.document.get("corpus_hash") != PREVIOUS_CORPUS_HASH:
        return (
            True,
            False,
            "The saved version is unknown or its review receipt expired. Preserve its evidence and arrange a reviewed version transition.",
        )
    doc = row.document
    unreviewed = (
        doc.get("reviewed") == {}
        and doc.get("frozen") is False
        and "policy" in doc
        and doc["policy"] is None
        and doc.get("holdout_exposed") is False
        and not doc.get("policy_history")
        and "freeze_receipt" not in doc
        and "frozen_hash" not in doc
        and isinstance(doc.get("labels"), dict)
        and set(doc["labels"]) == set(CASES)
    )
    old_marker_known = (
        old is not None
        and old.kind == "holdout_marker"
        and old.document == {"corpus_hash": PREVIOUS_CORPUS_HASH, "exposed": False}
        and old.expires_at.replace(tzinfo=UTC) > datetime.now(UTC)
    )
    new_marker_safe = new is None or (
        new.kind == "holdout_marker"
        and new.document == {"corpus_hash": CORPUS_HASH, "exposed": False}
        and new.expires_at.replace(tzinfo=UTC) > datetime.now(UTC)
    )
    eligible = (
        unreviewed
        and old_marker_known
        and new_marker_safe
        and state["retained_evidence"] == 0
        and (new is not None or state["marker_count"] < 100)
    )
    return (
        True,
        eligible,
        (
            "Revised version 2 strengthens AI-proposed evidence. This unreviewed draft can be replaced explicitly; no human labels are confirmed."
            if eligible
            else "Existing review, policy, exposure or retained evaluation evidence prevents automatic draft replacement. Preserve that evidence and arrange a reviewed version transition."
        ),
    )


def compatibility_view(db):
    state = repository.compatibility_state(db, _marker_id(PREVIOUS_CORPUS_HASH), _marker_id(CORPUS_HASH))
    required, eligible, detail = _compatibility(state)
    row = state["review"]
    return {
        "upgrade_required": required,
        "eligible": eligible,
        "previous_corpus_hash": row.document.get("corpus_hash") if row else None,
        "corpus_hash": CORPUS_HASH,
        "detail": detail,
    }


def upgrade_draft(db, payload):
    UpgradeCommand.model_validate(payload)
    state = repository.compatibility_state(db, _marker_id(PREVIOUS_CORPUS_HASH), _marker_id(CORPUS_HASH), lock=True)
    required, eligible, _ = _compatibility(state)
    if not required:
        db.rollback()
        return corpus_view(db)
    if not eligible:
        db.rollback()
        raise ValueError(
            "Only the known unreviewed, unexposed v1 draft without retained run/history evidence can transition"
        )
    repository.replace_unreviewed_draft(
        db,
        state["review"],
        _draft_document(),
        _marker_id(CORPUS_HASH),
        {
            "previous_corpus_hash": PREVIOUS_CORPUS_HASH,
            "corpus_hash": CORPUS_HASH,
            "version": CORPUS["version"],
            "at": datetime.now(UTC).isoformat(),
        },
    )
    return corpus_view(db)


def _marker(db):
    key = _marker_id(CORPUS_HASH)
    marker = repository.read(db, key)
    if marker is None:
        try:
            marker = repository.create(db, "holdout_marker", {"corpus_hash": CORPUS_HASH, "exposed": False}, key=key)
        except IntegrityError:
            db.rollback()
            marker = repository.read(db, key)
            if marker is None:
                raise ValueError("Concurrent marker initialization; refresh") from None
    return marker


def _review(db):
    compatibility = compatibility_view(db)
    if compatibility["upgrade_required"]:
        raise ValueError("Corpus version changed; explicit eligible draft transition or evidence review required")
    marker = _marker(db)
    row = repository.read(db, repository.REVIEW_ID)
    if row is None:
        document = _draft_document(marker.document["exposed"])
        try:
            row = repository.create(db, "review", document, key=repository.REVIEW_ID)
        except IntegrityError:
            db.rollback()
            row = repository.read(db, repository.REVIEW_ID)
            if row is None:
                raise ValueError("Concurrent review initialization; refresh") from None
    if row.document["corpus_hash"] != CORPUS_HASH:
        raise ValueError("Corpus version changed; retain/export previous evidence and begin a new review")
    if marker.document["exposed"] and not row.document.get("holdout_exposed"):
        doc = copy.deepcopy(row.document)
        doc["holdout_exposed"] = True
        repository.change(db, row, doc)
        row = repository.read(db, repository.REVIEW_ID)
    return row


def review_hash(document):
    return digest(
        {
            "corpus_hash": document["corpus_hash"],
            "labels": document["labels"],
            "reviewed": document["reviewed"],
            "splits": {key: case["split"] for key, case in CASES.items()},
        }
    )


def corpus_view(db: Session):
    doc = _review(db).document
    diagnostic = copy.deepcopy(doc.get("diagnostic"))
    if diagnostic:
        allocation = _marker(db).document.get("diagnostic", {})
        diagnostic = {
            key: diagnostic[key]
            for key in (
                "authorization_hash",
                "label_provenance",
                "selected_count",
                "selected_reviewed_count",
                "split_counts",
            )
        }
        diagnostic["split_used"] = allocation.get("split_used", {"train": True, "holdout": True})
    return {
        "corpus_hash": CORPUS_HASH,
        "review_hash": review_hash(doc),
        "version": CORPUS["version"],
        "status": CORPUS["status"],
        "adequacy": CORPUS["adequacy"],
        "frozen": doc["frozen"],
        "freeze_kind": doc.get("freeze_kind", "human_reviewed" if doc["frozen"] else None),
        "diagnostic": diagnostic,
        "diagnostic_policy": DIAGNOSTIC_POLICY,
        "reviewed_count": len(doc["reviewed"]),
        "case_count": len(CASES),
        "label_options": LABELS,
        "rubrics": CORPUS["rubrics"],
        "policy": doc["policy"],
        "limits": {"max_cases": CASE_LIMIT, "max_requests": CASE_LIMIT, "max_seconds": 1800},
        "holdout_exposed": doc.get("holdout_exposed", False),
        "cases": [
            {**case, "label": doc["labels"][key], "reviewed": key in doc["reviewed"]} for key, case in CASES.items()
        ],
        "group_count": len({case["leakage_group"] for case in CASES.values()}),
    }


def review_case(db, payload, actor):
    command = ReviewCommand.model_validate(payload)
    row = _review(db)
    doc = copy.deepcopy(row.document)
    if doc["frozen"] or command.corpus_hash != CORPUS_HASH or command.review_hash != review_hash(doc):
        raise ValueError("Frozen or changed evidence; refresh before reviewing")
    case = CASES.get(command.case_id)
    if not case or command.label not in LABELS[case["task"]]:
        raise ValueError("Unknown case or label")
    doc["labels"][command.case_id] = command.label
    doc["reviewed"][command.case_id] = {"actor_id": str(actor), "at": datetime.now(UTC).isoformat()}
    repository.change(db, row, doc)
    return corpus_view(db)


def freeze(db, payload, actor):
    command = FreezeCommand.model_validate(payload)
    row = _review(db)
    doc = copy.deepcopy(row.document)
    if doc["frozen"] or command.review_hash != review_hash(doc) or len(doc["reviewed"]) != len(CASES):
        raise ValueError("Review every case and confirm the current exact hash before freezing")
    doc.update(
        frozen=True,
        freeze_kind="human_reviewed",
        frozen_hash=command.review_hash,
        freeze_receipt={"actor_id": str(actor), "at": datetime.now(UTC).isoformat(), "hash": command.review_hash},
    )
    repository.change(db, row, doc)
    return corpus_view(db)


def diagnostic_freeze(db, payload, actor):
    """Owner authorization of provisional reference measurements, NEVER label review."""
    command = FreezeCommand.model_validate(payload)
    row = _review(db)
    doc = copy.deepcopy(row.document)
    if doc["frozen"] or doc.get("holdout_exposed") or doc.get("policy") or command.review_hash != review_hash(doc):
        raise ValueError("Diagnostic snapshot requires unchanged, unfrozen, unexposed evidence")
    config = classifier_service.config_view(db)
    if not config["configured"] or not config["valid"] or not config["config"]["enabled"]:
        raise ValueError("Configure the selected classifier before diagnostic authorization")
    selected = [key for key, case in CASES.items() if case["task"] in DIAGNOSTIC_TASKS]
    counts = {split: sum(CASES[key]["split"] == split for key in selected) for split in DIAGNOSTIC_SPLITS}
    if counts != {split: limits[0] for split, limits in DIAGNOSTIC_SPLITS.items()}:
        raise ValueError("This allocation belongs only to the fixed 206-case reference snapshot")
    snapshot = {
        "case_ids": selected,
        "labels": {key: doc["labels"][key] for key in selected},
        "sources": {key: "human_reviewed" if key in doc["reviewed"] else "ai_proposed" for key in selected},
        "review_hash": command.review_hash,
        "corpus_hash": CORPUS_HASH,
        "content_hash": content_fingerprint(),
        "source_sha": _source_revision(),
        "config_revision": connector_config.classifier_revision(db),
        "target": _config_snapshot(db),
        "label_provenance": "mixed_reference"
        if any(key in doc["reviewed"] for key in selected)
        else "ai_proposed_reference",
        "selected_count": len(selected),
        "selected_reviewed_count": sum(key in doc["reviewed"] for key in selected),
        "split_counts": counts,
        "fixed_policy": DIAGNOSTIC_POLICY,
    }
    authorization_hash = digest(snapshot)
    doc.update(frozen=True, freeze_kind="owner_authorized_diagnostic", frozen_hash=command.review_hash)
    doc["diagnostic"] = {
        **snapshot,
        "authorization_hash": authorization_hash,
        "authorized_by": str(actor),
        "authorized_at": datetime.now(UTC).isoformat(),
    }
    repository.diagnostic_freeze(db, row, doc, _marker(db).id, authorization_hash)
    return corpus_view(db)


def reopen(db, payload, actor):
    command = FreezeCommand.model_validate(payload)
    row = _review(db)
    doc = copy.deepcopy(row.document)
    if command.review_hash != review_hash(doc):
        raise ValueError("Review hash changed")
    # Preserve prior human attestations separately; never mutate a frozen receipt.
    archive = {**doc, "reopened_by": str(actor), "reopened_at": datetime.now(UTC).isoformat()}
    doc.update(frozen=False, policy=None, reviewed={})
    doc.pop("freeze_receipt", None)
    doc.pop("frozen_hash", None)
    doc.pop("freeze_kind", None)
    doc.pop("diagnostic", None)
    repository.archive_review(db, row, doc, archive)
    return corpus_view(db)


def set_policy(db, payload):
    command = PolicyCommand.model_validate(payload)
    row = _review(db)
    doc = copy.deepcopy(row.document)
    if (
        not doc["frozen"]
        or doc.get("holdout_exposed")
        or command.review_hash != doc["frozen_hash"]
        or set(command.tasks) - set(LABELS)
    ):
        raise ValueError("Freeze reviewed grouped holdout before selecting task thresholds")
    # Training may select a new revision; every run retains its original snapshot.
    # Once any holdout outcome is exposed, ALL retuning requires independent data.
    history = doc.get("policy_history", [])
    if doc["policy"] and len(history) >= 19:
        raise ValueError("Policy revision limit reached")
    if doc["policy"]:
        history.append(doc["policy"])
    doc["policy_history"] = history
    tasks = {key: value.model_dump() for key, value in command.tasks.items()}
    if doc.get("diagnostic"):
        if doc["policy"] or tasks != {task: DIAGNOSTIC_POLICY for task in DIAGNOSTIC_TASKS}:
            raise ValueError("Diagnostic policy is fixed; no tuning or extra tasks authorized")
        _validate_diagnostic(doc, db)
    target = _config_snapshot(db)
    doc["policy"] = {
        "version": "task-policy.v1",
        "tasks": tasks,
        "target": target,
        "hash": digest({"tasks": tasks, "target": target}),
        "selected_at": datetime.now(UTC).isoformat(),
    }
    repository.change(db, row, doc)
    return corpus_view(db)


def _config_snapshot(db):
    view = classifier_service.config_view(db)
    return {key: view["config"][key] for key in ("provider", "model", "execution_location", "endpoint")}


def _validate_diagnostic(doc, db):
    snapshot = doc.get("diagnostic")
    if snapshot and (
        doc.get("freeze_kind") != "owner_authorized_diagnostic"
        or not isinstance(snapshot.get("source_sha"), str)
        or not doc["frozen"]
        or doc.get("frozen_hash") != snapshot["review_hash"]
        or review_hash(doc) != snapshot["review_hash"]
        or snapshot["content_hash"] != content_fingerprint()
        or snapshot["config_revision"] != connector_config.classifier_revision(db)
        or snapshot["target"] != _config_snapshot(db)
    ):
        raise ValueError("Diagnostic snapshot/configuration changed; this allocation cannot be reused")


def create_run(db, payload, actor):
    command = RunCommand.model_validate(payload)
    review_row = _review(db)
    review_revision = review_row.revision
    doc = review_row.document
    if not command.budget_confirmed:
        raise ValueError("Explicit request-count and elapsed-time budget confirmation required")
    if command.engine == "classifier" and (not doc["frozen"] or not doc["policy"]):
        raise ValueError("Human review, frozen holdout and task policy are required for live runs")
    if command.split == "holdout" and (not doc["frozen"] or not doc["policy"]):
        raise ValueError("Freeze human labels and policy before exposing any holdout metrics")
    config = _config_snapshot(db)
    if command.engine == "classifier":
        view = classifier_service.config_view(db)
        if not (
            view["valid"]
            and view["configured"]
            and view["config"]["enabled"]
            and (view["config"]["execution_location"] == "local" or view["config"]["cloud_opt_in"])
        ):
            raise ValueError("Configure and explicitly enable the selected classifier first")
        if doc["policy"]["target"] != config:
            raise ValueError(
                "Task policy belongs to another provider/model/location; configure its own policy before live evaluation"
            )
    selected = [key for key, case in CASES.items() if case["split"] == command.split]
    if doc["policy"]:
        selected = [key for key in selected if CASES[key]["task"] in doc["policy"]["tasks"]]
    diagnostic = doc.get("diagnostic")
    claim = None
    if diagnostic:
        _validate_diagnostic(doc, db)
        if (
            command.engine != "classifier"
            or not doc["policy"]
            or (command.max_requests, command.max_seconds) != DIAGNOSTIC_SPLITS[command.split]
        ):
            raise ValueError(
                "Diagnostic allocation requires the fixed classifier split budget; no other engines or reruns"
            )
        selected = [key for key in diagnostic["case_ids"] if CASES[key]["split"] == command.split]
        claim = (_marker_id(CORPUS_HASH), diagnostic["authorization_hash"], command.split, review_revision)
    elif _marker(db).document.get("diagnostic"):
        raise ValueError("Diagnostic allocation cannot be replayed through another review mode")
    now = datetime.now(UTC).isoformat()
    document = {
        "status": "ready",
        "engine": command.engine,
        "evidence_kind": ("contract_fixture" if TRANSPORT_FIXTURE else "live_provider")
        if command.engine == "classifier"
        else "rules"
        if command.engine == "rules"
        else "unavailable",
        "corpus_hash": CORPUS_HASH,
        "review_hash": review_hash(doc),
        "human_reviewed": doc["frozen"] and not diagnostic,
        "freeze_kind": doc.get("freeze_kind", "human_reviewed" if doc["frozen"] else None),
        "diagnostic_authorization_hash": diagnostic["authorization_hash"] if diagnostic else None,
        "label_provenance": diagnostic["label_provenance"]
        if diagnostic
        else "human_reviewed"
        if doc["frozen"]
        else "ai_proposed",
        "reference_sources": {key: diagnostic["sources"][key] for key in selected} if diagnostic else None,
        "selected_reviewed_count": sum(diagnostic["sources"][key] == "human_reviewed" for key in selected)
        if diagnostic
        else None,
        "metric_interpretation": "Agreement with provisional synthetic references; not human-ground-truth accuracy"
        if diagnostic
        else "Comparison against the recorded reference labels",
        "config_revision": diagnostic["config_revision"] if diagnostic else connector_config.classifier_revision(db),
        "policy_hash": doc["policy"]["hash"] if doc["policy"] else None,
        "policy": doc["policy"],
        "labels": doc["labels"],
        "case_ids": selected,
        "versions": {
            "source_sha": diagnostic["source_sha"] if diagnostic else _source_revision(),
            "content_hash": diagnostic["content_hash"] if diagnostic else content_fingerprint(),
            "corpus": CORPUS["version"],
            "prompt": PROMPT_VERSION,
            "contract": "classifier.v1",
            **(diagnostic["target"] if diagnostic else config),
        },
        "split": command.split,
        "started_at": now,
        "lease": None,
        "budget": {
            "unit": "requests",
            "max_requests": command.max_requests,
            "max_seconds": command.max_seconds,
            "attempted": 0,
            "elapsed_seconds": 0,
            "approved_by": str(actor),
            "approved_at": now,
        },
        "completed": 0,
        "total": len(selected),
        "outcomes": [],
        "detail": "Synthetic evidence only; no pantry data changes. Human correction effort is unmeasured.",
    }
    row = repository.create(db, "run", document, diagnostic_claim=claim)
    return read_run(db, row.id)


def _read(db, key):
    row = repository.read(db, key)
    if row is None or row.kind != "run":
        raise ValueError("Benchmark expired or unavailable")
    return row


def read_run(db, key):
    row = _read(db, key)
    doc = copy.deepcopy(row.document)
    if doc["status"] == "running" and time.time() - doc["lease"]["at"] > 12:
        doc.update(
            status="interrupted",
            lease=None,
            detail="Interrupted execution; create a new explicit run. No automatic retry.",
        )
        repository.change(db, row, doc)
    metrics = summarize(doc["outcomes"])
    for metric in metrics.values():
        metric["reference_basis"] = doc.get(
            "label_provenance", "human_reviewed" if doc["human_reviewed"] else "ai_proposed"
        )
    rollout = {}
    for task in LABELS:
        reasons = ["Explicit evidence/benefit review and a later review-flow slice are required"]
        metric = metrics.get(task)
        policy = (doc["policy"] or {}).get("tasks", {}).get(task)
        checks = {}
        if not doc["human_reviewed"]:
            reasons.append("AI-proposed labels have not been confirmed by a human")
        if doc["evidence_kind"] != "live_provider":
            reasons.append("No live provider evidence")
        if doc["split"] != "holdout":
            reasons.append("Not frozen held-out evidence")
        if doc["completed"] != doc["total"] or doc["status"] != "completed":
            reasons.append("Run incomplete")
        compatible_corpus = doc["corpus_hash"] == CORPUS_HASH
        if not compatible_corpus:
            reasons.append(
                "Historical corpus version differs; numerical checks are unknown and are not reinterpreted against current proposals"
            )
        elif not policy or not metric:
            reasons.append("Task policy or measured support unavailable")
        else:
            critical_required = {
                case_id
                for case_id in doc["case_ids"]
                if CASES[case_id]["task"] == task
                and CASES[case_id]["critical_negative"]
                and doc["labels"][case_id] not in {"same", "eligible", "stock", "ingredient"}
            }
            critical_valid = {
                item["case_id"] for item in doc["outcomes"] if item["task"] == task and item.get("valid_response")
            }
            checks = {
                "valid_support": metric["valid_responses"] >= policy["min_support"],
                "independent_groups": metric["valid_groups"] >= policy["min_groups"],
                "critical_negatives_covered": bool(critical_required) and critical_required <= critical_valid,
                "no_new_false_matches": metric["false_positives"] <= policy["max_false_positives"]
                if metric["valid_responses"]
                else None,
                "p95_latency": metric["p95_ms"] <= policy["max_p95_ms"] if metric["p95_ms"] is not None else None,
                "request_failures": metric["failures"] / metric["request_attempts"] <= policy["max_failure_rate"]
                if metric["request_attempts"]
                else None,
                "request_budget": metric["support"] <= policy["max_requests"],
                "complete_run": doc["status"] == "completed" and doc["completed"] == doc["total"],
            }
            if not all(value is True for value in checks.values()):
                reasons.append("Task-specific measured checks failed or remain unknown")
        if task == "contextual_substitution":
            reasons.append("Tracker #100 requires a separate benefit and approval-scope decision")
        rollout[task] = {
            "eligible": False,
            "numeric_checks_passed": (bool(checks) and all(value is True for value in checks.values()))
            if compatible_corpus
            else None,
            "checks": checks,
            "reasons": reasons,
        }
    # Do not expose reviewer identities, internal leases or duplicated target label map.
    for field in ("labels", "case_ids", "lease", "policy", "config_revision"):
        doc.pop(field, None)
    return {"id": str(key), **doc, "summary": {"by_task": metrics, "rollout": rollout}}


def list_runs(db):
    return {"runs": [read_run(db, row.id) for row in repository.recent(db)]}


def remove_run(db, key):
    repository.remove(db, key)


def cancel(db, key):
    row = _read(db, key)
    doc = copy.deepcopy(row.document)
    if doc["status"] in {"ready", "running"}:
        doc.update(status="cancelled", detail="Cancelled; reserved attempts may still be billed. No automatic retry.")
        repository.change(db, row, doc)
    return read_run(db, key)


def _baseline(case):
    # These projections execute current public rules, never consult a target label.
    task, evidence = case["task"], case["evidence"]
    if task == "culinary_identity":
        from modules.catalog.services.canonical import canonical_ingredient

        left, right = map(canonical_ingredient, (evidence["subject"], evidence["candidate_or_context"]))
        return ("same", "canonical_key_equal") if left and left == right else (None, "canonical_no_exact_match")
    if task == "receipt_role":
        from modules.automation.receipt_parser import parse_receipt_text

        return (
            ("stock", "parser_stock_candidate")
            if parse_receipt_text(evidence["subject"])
            else (None, "parser_no_stock_candidate")
        )
    if task == "recipe_plausibility":
        from modules.automation.recipe_import_parser import parse_recipe_text

        text = "Synthetic Recipe\nIngredients\n" + evidence["subject"] + "\nInstructions\n"
        return (
            ("ingredient", "parser_ingredient_candidate")
            if parse_recipe_text(text)["ingredients"]
            else (None, "parser_no_ingredient_candidate")
        )
    return None, "unsupported_task"


def _receipt_matches(receipt, run):
    if not receipt or not receipt.get("frozen") or receipt.get("frozen_hash") != run["review_hash"]:
        return False
    expected = run.get("diagnostic_authorization_hash")
    if expected:
        snapshot = receipt.get("diagnostic", {})
        return (
            receipt.get("freeze_kind") == "owner_authorized_diagnostic"
            and snapshot.get("authorization_hash") == expected
            and snapshot.get("review_hash") == run["review_hash"]
            and snapshot.get("config_revision") == run["config_revision"]
            and snapshot.get("content_hash") == run["versions"]["content_hash"]
            and snapshot.get("source_sha") == run["versions"]["source_sha"]
            and snapshot.get("target")
            == {key: run["versions"][key] for key in ("provider", "model", "execution_location", "endpoint")}
            and (receipt.get("policy") or {}).get("hash") == run["policy_hash"]
            and (receipt.get("policy") or {}).get("target") == snapshot.get("target")
            and not run["human_reviewed"]
        )
    return receipt.get("freeze_kind") != "owner_authorized_diagnostic"


async def step(db, key, disconnected):
    row = _read(db, key)
    doc = copy.deepcopy(row.document)
    admission_revision = row.revision
    if doc["status"] != "ready":
        raise ValueError("Run not ready; concurrent steps and automatic retries are forbidden")
    if doc["versions"]["content_hash"] != content_fingerprint():
        raise ValueError("Shipped benchmark content changed; create a new run")
    if doc["engine"] == "classifier" and (doc["evidence_kind"] == "contract_fixture") != TRANSPORT_FIXTURE:
        raise ValueError("Transport evidence context changed; create a new run")
    elapsed = (datetime.now(UTC) - datetime.fromisoformat(doc["started_at"])).total_seconds()
    if doc["budget"]["attempted"] >= doc["budget"]["max_requests"] or elapsed >= doc["budget"]["max_seconds"]:
        doc.update(status="interrupted", detail="Approved request/time budget exhausted; create a new explicit run.")
        repository.change(db, row, doc)
        return read_run(db, key)
    if doc["versions"] != {**doc["versions"], **_config_snapshot(db)} and doc["engine"] == "classifier":
        raise ValueError("Provider configuration changed; create a new run")
    if doc["engine"] == "classifier" and doc["config_revision"] != connector_config.classifier_revision(db):
        raise ValueError("Settings or credentials changed; create a new run and approve a new budget")
    config = classifier_service._config(db) if doc["engine"] == "classifier" else None
    needs_receipt = doc["engine"] == "classifier" or doc["split"] == "holdout"
    if needs_receipt:
        receipt = _review(db).document
        if not _receipt_matches(receipt, doc):
            raise ValueError("Human review receipt was reopened or changed; create a new run")
    case = CASES[doc["case_ids"][doc["completed"]]]
    task_policy = (doc["policy"] or {}).get("tasks", {}).get(case["task"])
    if task_policy and sum(item["task"] == case["task"] for item in doc["outcomes"]) >= task_policy["max_requests"]:
        doc.update(status="interrupted", detail="Task request budget exhausted; no automatic retry.")
        repository.change(db, row, doc)
        return read_run(db, key)
    if doc["split"] == "holdout":
        review_row = _review(db)
        review_doc = copy.deepcopy(review_row.document)
        if not review_doc.get("holdout_exposed"):
            marker = _marker(db)
            if not marker.document["exposed"]:
                repository.change(db, marker, {**marker.document, "exposed": True})
                review_row = _review(db)
                review_doc = copy.deepcopy(review_row.document)
            review_doc["holdout_exposed"] = True
            repository.change(db, review_row, review_doc)
            # change expires ORM state; refresh optimistic revision for the run.
            row = _read(db, key)
            if row.revision != admission_revision or row.document["status"] != "ready":
                raise ValueError("Another request reserved this run; refresh")
    lease = str(uuid4())
    doc.update(status="running", lease={"id": lease, "at": time.time()})
    doc["budget"]["attempted"] += 1
    doc["budget"]["elapsed_seconds"] = elapsed
    # Immutable admission revision survives any intermediate review/marker commit
    # and ORM expiration. A refreshed row must never authorize a stale command.
    repository.change(db, row, doc, expected_revision=admission_revision)
    start = time.monotonic()
    outcome = {
        "case_id": case["id"],
        "task": case["task"],
        "group": case["leakage_group"],
        "expected": doc["labels"][case["id"]],
        "critical_negative": case["critical_negative"],
        "status": "abstain",
        "prediction": None,
        "usage": {},
        "network_attempted": False,
    }
    cancelled = False

    async def watch(scope):
        nonlocal cancelled
        while True:
            await anyio.sleep(0.1)
            with SessionLocal() as check_db:
                current = repository.read(check_db, key)
                stop = current is None or current.document["status"] == "cancelled"
                if needs_receipt:
                    receipt_row = repository.read(check_db, repository.REVIEW_ID)
                    stop = stop or receipt_row is None or not _receipt_matches(receipt_row.document, doc)
                if config is not None:
                    stop = stop or connector_config.classifier_revision(check_db) != doc["config_revision"]
            if stop or await disconnected():
                cancelled = True
                scope.cancel()
                return

    try:
        with anyio.move_on_after(min(8.0, doc["budget"]["max_seconds"] - elapsed)) as scope:
            async with anyio.create_task_group() as group:
                group.start_soon(watch, scope)
                if doc["engine"] == "rules":
                    prediction, reason = _baseline(case)
                    outcome.update(prediction=prediction, status="ok" if prediction else "abstain", reason=reason)
                elif doc["engine"] == "legacy_judge":
                    from modules.automation import food_judge

                    reason = (
                        "legacy_judge_unconfigured"
                        if not food_judge.available(db)
                        else "legacy_judge_not_measured_cancellable_baseline_unavailable"
                    )
                    outcome.update(status="unavailable", reason=reason)
                else:
                    # Use the exact validated in-memory config, never a newly
                    # loaded key/provider after the reservation's commit.
                    with SessionLocal() as admission_db:
                        receipt_row = repository.read(admission_db, repository.REVIEW_ID)
                        admitted = (
                            connector_config.classifier_revision(admission_db) == doc["config_revision"]
                            and receipt_row is not None
                            and _receipt_matches(receipt_row.document, doc)
                        )
                    if (
                        not admitted
                        or not config.enabled
                        or (config.execution_location == "cloud_direct" and not config.api_key)
                    ):
                        raise ClassifierFailure("unavailable", "configuration_disabled_or_unavailable")
                    if config.execution_location != "local" and not config.cloud_opt_in:
                        raise ClassifierFailure("abstain", "cloud_consent_required")
                    request = EvaluationRequest.model_validate(request_for(case))
                    outcome["network_attempted"] = True
                    response = await (
                        jev.evaluate_async(config, request)
                        if config.provider == "openrouter"
                        else clef.evaluate_async(config, request)
                    )
                    answers = response["answers"] if config.provider == "openrouter" else response
                    outcome["valid_response"] = True
                    answer = answers["q_1"]
                    labels = LABELS[case["task"]]
                    probabilities = sorted(answer["probabilities"].values(), reverse=True)
                    # Bounded validated labels/scalars permit operator audit of
                    # abstentions without storing provider payloads or rationale.
                    outcome.update(
                        raw_prediction=labels[int(answer["choice"].split("_")[1]) - 1],
                        runner_up_probability=probabilities[1],
                        decision_margin=probabilities[0] - probabilities[1],
                    )
                    policy = doc["policy"]["tasks"].get(case["task"])
                    if (
                        policy
                        and probabilities[0] >= policy["confidence"]
                        and probabilities[0] - probabilities[1] >= policy["margin"]
                    ):
                        outcome.update(status="ok", prediction=outcome["raw_prediction"])
                    outcome["probability"] = probabilities[0]
                    if config.provider == "openrouter":
                        outcome["usage"] = response.get("usage") or {}
                        outcome["resolved_model"] = response.get("resolved_model")
                    else:
                        outcome["resolved_model"] = config.model
                group.cancel_scope.cancel()
        if scope.cancel_called:
            outcome.update(
                status="cancelled" if cancelled else "unavailable",
                reason="cancelled" if cancelled else "timeout",
                prediction=None,
            )
    except* ClassifierFailure as errors:
        failure = errors.exceptions[0]
        outcome.update(status=failure.status, reason=failure.code, prediction=None)
    outcome["latency_ms"] = round((time.monotonic() - start) * 1000, 3)
    db.expire_all()
    row = _read(db, key)
    current = copy.deepcopy(row.document)
    if current["lease"] and current["lease"]["id"] == lease:
        current["outcomes"].append(outcome)
        current["completed"] += 1
        current["budget"]["elapsed_seconds"] = (
            datetime.now(UTC) - datetime.fromisoformat(current["started_at"])
        ).total_seconds()
        current.update(
            lease=None,
            status="cancelled"
            if cancelled or current["status"] == "cancelled"
            else "completed"
            if current["completed"] == current["total"]
            else "ready",
        )
        repository.change(db, row, current)
    return read_run(db, key)
