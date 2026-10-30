"""Explicit bounded classifier hints and durable human-reviewed receipt commit."""

import hashlib
import json
import re
import secrets
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from difflib import SequenceMatcher
from uuid import UUID, uuid4

from sqlalchemy.orm import Session

from app.core.crypto import SecretDecryptError, SecretStorageUnavailable
from modules.automation.classifier_service import config_view, run_evaluation
from modules.catalog.services.barcodes import resolve_local_item
from modules.catalog.services.items import list_items
from modules.catalog.services.match_evidence import item_evidence
from modules.shopping import receipt_review_repository as repository
from modules.shopping.receipt_review_models import ReceiptReviewDraft
from modules.shopping.services.connector_config import classifier_snapshot
from modules.shopping.services.receipts import commit_receipt_lines

VERSION = "receipt-review.v1"
WARNING = "Model scores are not validated accuracy. Verify every suggestion before confirming."
FINANCIAL = re.compile(
    r"^(?:(?:sub\s*total|grand\s+total|total|sales\s+tax|tax|coupon|discount|savings|deposit|bottle\s+(?:fee|deposit)|change|cash|credit|debit|balance\s+due|amount\s+due)(?:\s*[:,]\s*|\s+)[+-]?\$?\d+(?:\.\d+)?\s*)$",
    re.I,
)
RETURN = re.compile(r"\b(?:return(?:ed)?|refund|void)\b|(?:^|[\s,])(?:[-−]\s*\$?|\$\s*[-−])\d", re.I)


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str, separators=(",", ":")).encode()).hexdigest()


def source_role(raw):
    if FINANCIAL.search(raw.strip()):
        return "financial_adjustment"
    if RETURN.search(raw):
        return "return"
    return None


def create_draft(db, *, actor_id, preview, location_id, raw_text, source_filename):
    lines = []
    remaining = list(preview["lines"])
    # Preserve partial parser successes and all nonblank unmatched OCR text.
    raw_lines = raw_text.splitlines() if raw_text else [r["raw_line"] for r in remaining]
    if len(raw_lines) > 200 or any(len(r) > 4000 for r in raw_lines) or sum(len(r) for r in raw_lines) > 64000:
        raise ValueError("Receipt is too large; split it into at most 200 lines / 64 KiB")
    for raw in raw_lines:
        if not raw.strip():
            continue
        index = next((i for i, r in enumerate(remaining) if r["raw_line"].strip() == raw.strip()), None)
        parsed = remaining.pop(index) if index is not None else None
        line = dict(
            parsed
            or {
                "raw_line": raw,
                "name": raw[:200],
                "quantity": "1",
                "unit": "each",
                "matched_item_id": None,
                "source": "rules",
            }
        )
        barcode = re.search(r"(?<!\d)\d{8,14}(?!\d)", raw)
        exact_item = resolve_local_item(db, barcode.group()) if barcode and not source_role(raw) else None
        if exact_item and exact_item.is_active:
            line.update(matched_item_id=str(exact_item.id), matched_item_name=exact_item.name)
        try:
            source_nonpositive = Decimal(str(line["quantity"])) <= 0
        except (InvalidOperation, ValueError):
            source_nonpositive = False
        role = source_role(raw) or ("return" if source_nonpositive else "stock" if parsed or exact_item else "unknown")
        line.setdefault("source", "rules")
        line.update(
            line_id=str(uuid4()),
            role=role,
            excluded=False,
            confirmed=False,
            create_new=False,
            annotation=None,
            source_nonstock=bool(source_role(raw) or source_nonpositive),
        )
        if line.get("matched_item_id"):
            line["matched_item_id"] = str(line["matched_item_id"])
        lines.append(line)
    if not lines:
        raise ValueError("Receipt has no nonempty lines")
    token = secrets.token_urlsafe(32)
    doc = {
        "version": VERSION,
        "location_id": str(location_id),
        "raw_text": raw_text,
        "source_filename": source_filename,
        "engine": preview.get("engine", "rules"),
        "lines": lines,
    }
    row = ReceiptReviewDraft(
        id=uuid4(),
        actor_id=actor_id,
        token_hash=fingerprint(token),
        expires_at=datetime.now(UTC) + timedelta(hours=24),
        revision=0,
        document=doc,
    )
    repository.create(db, row)
    return {**doc, "draft_id": str(row.id), "token": token, "line_count": len(lines), "warning": WARNING}


def _check(row, lines):
    if row is None:
        raise LookupError("Receipt draft not found")
    expires = row.expires_at.replace(tzinfo=UTC) if row.expires_at.tzinfo is None else row.expires_at
    if expires <= datetime.now(UTC):
        raise ValueError("Receipt draft expired; preview again")
    original = row.document["lines"]
    if len(lines) != len(original) or len({review_line["line_id"] for review_line in lines}) != len(lines):
        raise ValueError("Receipt source changed; preview again")
    by_id = {review_line["line_id"]: review_line for review_line in original}
    for line in lines:
        if line["line_id"] not in by_id or line["raw_line"] != by_id[line["line_id"]]["raw_line"]:
            raise ValueError("Receipt source changed; preview again")


def _safe_snapshot(db):
    try:
        return classifier_snapshot(db)
    except (ValueError, SecretDecryptError, SecretStorageUnavailable):
        db.rollback()
        return None


def review_draft(db, *, draft_id, actor_id, token, lines):
    row = repository.read(db, draft_id, actor_id, fingerprint(token))
    _check(row, lines)
    if row.document.get("commit_response"):
        raise ValueError("Receipt already committed")
    return annotate_lines(db, lines)


def annotate_lines(db, lines, *, source_context="Receipt draft"):
    """Shopping-owned advisory interface; caller owns source/actor binding."""
    # No draft lock is held across provider I/O, and provider services own only
    # their diagnostic records. Never overwrite the source/choices in this call.
    snapshot = _safe_snapshot(db)
    if snapshot is None:
        return {
            "version": VERSION,
            "annotations": [],
            "provider_requests": 0,
            "status": "unavailable",
            "warning": WARNING,
        }
    view = config_view(db)
    config = view["config"]
    enabled = view["valid"] and config["enabled"] and config["runtime_review_enabled"]
    catalog = [{"id": str(i.id), "name": i.name} for i in list_items(db, limit=50)]
    db.rollback()
    annotations = []
    calls = 0
    all_source_versions = {}
    for line in lines:
        if source_role(line["raw_line"]):
            annotations.append(
                {
                    "line_id": line["line_id"],
                    "status": "rules",
                    "binding": fingerprint(line),
                    "role": source_role(line["raw_line"]),
                    "candidates": [],
                    "score": None,
                }
            )
            continue
        if line["excluded"] or (
            line["matched_item_id"] and line["role"] == "stock" and not source_role(line["raw_line"])
        ):
            continue
        if calls >= 8:
            annotations.append(
                {"line_id": line["line_id"], "status": "limit", "binding": fingerprint(line), "candidates": []}
            )
            continue
        ranked = sorted(
            catalog, key=lambda i: SequenceMatcher(None, line["name"].lower(), i["name"].lower()).ratio(), reverse=True
        )[:4]
        candidates = [
            item for item in ranked if SequenceMatcher(None, line["name"].lower(), item["name"].lower()).ratio() >= 0.45
        ]
        annotation = {
            "line_id": line["line_id"],
            "binding": fingerprint(line),
            "status": "disabled",
            "candidates": candidates,
            "role": None,
            "suggested_item_id": None,
            "score": None,
            "below_threshold": True,
            "threshold": config["review_confidence"],
        }
        if enabled:
            if (_safe_snapshot(db) or {}).get("revision") != snapshot["revision"]:
                db.rollback()
                return {
                    "version": VERSION,
                    "annotations": [],
                    "provider_requests": calls,
                    "status": "stale_configuration",
                    "warning": WARNING,
                }
            db.rollback()
            candidate_context = {}
            source_versions = {}
            retained = []
            for candidate in candidates:
                try:
                    evidence = item_evidence(db, UUID(candidate["id"]))
                except LookupError:
                    continue
                retained.append(candidate)
                label = f"c_{len(retained)}"
                structured = evidence["structured_evidence"]
                source_versions[candidate["id"]] = evidence["source_fingerprint"]
                all_source_versions.setdefault(candidate["id"], evidence["source_fingerprint"])
                candidate_context[label] = {
                    "name": candidate["name"][:200],
                    "brand": (evidence.get("brand") or "Unknown")[:80],
                    "ingredients_text": (evidence.get("ingredients_text") or "Unknown")[:200],
                    "catalog_source": (evidence.get("source") or "Unknown")[:40],
                    "nutrition_basis": evidence["nutrition_basis"],
                    "nutrition_values": evidence["nutrition_values"],
                    "saved_product_facts": {
                        key: str(value)[:60] for key, value in structured["facts"].items() if value is not None
                    }
                    if not structured["stale"]
                    else {},
                    "saved_facts_provenance": "stale_not_used"
                    if structured["stale"]
                    else "user_provided"
                    if structured["revision"] > 0 and any(value is not None for value in structured["facts"].values())
                    else "unknown",
                    "missing_facts": "Unknown; do not invent evidence",
                    "context_truncated": True,
                }
            candidates = retained
            annotation["candidates"] = candidates
            db.rollback()
            criteria = {f"c_{i+1}": f"Candidate {i+1}: {c['name']}"[:500] for i, c in enumerate(candidates)}
            criteria.update(c_20="None of these catalog items", c_21="Not enough evidence to identify the item")
            result = run_evaluation(
                db,
                {
                    "state": json.dumps(
                        {
                            "raw_line": line["raw_line"][:1000],
                            "candidate_context": candidate_context,
                            "parsed_name": line["name"],
                            "quantity": line["quantity"],
                            "unit": line["unit"],
                            "context": f"{source_context}; quantity/price may be ambiguous. Candidate catalog facts describe candidates only, not evidence that the source line is that item. Never infer arithmetic. Unknown means missing evidence.",
                        },
                        ensure_ascii=False,
                    ),
                    "questions": {
                        "q_1": {
                            "type": "choice",
                            "instructions": "Which role is supported by the receipt line? Choose unknown when evidence is missing.",
                            "criteria": {
                                "c_1": "Stock purchase",
                                "c_2": "Financial adjustment / deposit / discount",
                                "c_3": "Return / refund / void",
                                "c_4": "Unknown or insufficient evidence",
                            },
                        },
                        "q_2": {
                            "type": "choice",
                            "instructions": "Which exact catalog identity is supported? Similarity alone is insufficient. Choose none or insufficient evidence when needed.",
                            "criteria": criteria,
                        },
                    },
                },
                expected_revision=snapshot["revision"],
            )
            calls += 1
            for candidate_id, source_version in source_versions.items():
                try:
                    current = item_evidence(db, UUID(candidate_id))["source_fingerprint"]
                except LookupError:
                    current = None
                if current != source_version:
                    db.rollback()
                    return {
                        "version": VERSION,
                        "annotations": [],
                        "provider_requests": calls,
                        "status": "stale_source",
                        "warning": WARNING,
                    }
            db.rollback()
            annotation.update(
                status=result["status"],
                reason_code=result["reason_code"],
                provider=result["provider"],
                model=result["model"],
                evaluation_id=result["id"],
            )
            if result["status"] == "ok":
                role = result["answers"].get("q_1", {})
                identity = result["answers"].get("q_2", {})
                annotation["role"] = {
                    "c_1": "stock",
                    "c_2": "financial_adjustment",
                    "c_3": "return",
                    "c_4": "unknown",
                }.get(role.get("choice"))
                choice = identity.get("choice")
                annotation["suggested_item_id"] = next(
                    (c["id"] for i, c in enumerate(candidates) if choice == f"c_{i+1}"), None
                )
                scores = [a.get("probabilities", {}).get(a.get("choice")) for a in (role, identity)]
                margins = []
                for answer in (role, identity):
                    ordered = sorted(answer.get("probabilities", {}).values(), reverse=True)
                    margins.append(ordered[0] - ordered[1] if len(ordered) > 1 else None)
                annotation.update(
                    role_score=scores[0], identity_score=scores[1], role_margin=margins[0], identity_margin=margins[1]
                )
                score = min(scores) if all(isinstance(s, int | float) for s in scores) else None
                annotation.update(score=score, below_threshold=score is None or score < config["review_confidence"])
        annotations.append(annotation)
    for candidate_id, source_version in all_source_versions.items():
        try:
            current = item_evidence(db, UUID(candidate_id))["source_fingerprint"]
        except LookupError:
            current = None
        if current != source_version:
            db.rollback()
            return {
                "version": VERSION,
                "annotations": [],
                "provider_requests": calls,
                "status": "stale_source",
                "warning": WARNING,
            }
    if (_safe_snapshot(db) or {}).get("revision") != snapshot["revision"]:
        db.rollback()
        return {
            "version": VERSION,
            "annotations": [],
            "provider_requests": calls,
            "status": "stale_configuration",
            "warning": WARNING,
        }
    db.rollback()
    return {"version": VERSION, "annotations": annotations, "provider_requests": calls, "warning": WARNING}


def commit_draft(db, *, draft_id, actor_id, token, lines):
    row = repository.locked(db, draft_id, actor_id, fingerprint(token))
    try:
        _check(row, lines)
        digest = fingerprint(lines)
        if row.document.get("commit_response"):
            if row.document["commit_hash"] != digest:
                raise ValueError("Receipt already committed with different choices")
            response = row.document["commit_response"]
            db.rollback()
            return response
        included = []
        for line in lines:
            if line["excluded"]:
                continue
            if not line["confirmed"] or line["role"] != "stock":
                raise ValueError("Confirm stock identity, amount and role or explicitly exclude every line")
            source_line = next(original for original in row.document["lines"] if original["line_id"] == line["line_id"])
            if source_role(line["raw_line"]) or source_line.get("source_nonstock"):
                raise ValueError("Financial/return source lines must be excluded; they cannot become positive stock")
            included.append(line)
        if not included:
            raise ValueError("No stock lines selected")
        # The preceding UPDATE starts an outer write transaction even on SQLite.
        # Legacy service commit() calls release savepoints only, never the outer
        # transaction: receipt/stock/draft receipt succeed or roll back together.
        deferred_events = []
        with Session(bind=db.connection(), join_transaction_mode="create_savepoint") as worker:
            response = commit_receipt_lines(
                worker,
                location_id=UUID(row.document["location_id"]),
                source_filename=row.document["source_filename"],
                raw_text=row.document["raw_text"],
                lines=included,
                deferred_events=deferred_events,
            )
        response = json.loads(json.dumps(response, default=str))
        row.document = {**row.document, "commit_hash": digest, "commit_response": response}
        db.commit()
        from core.events import get_event_bus

        get_event_bus().publish_all(deferred_events, db)
        return response
    except Exception:
        db.rollback()
        raise
