"""Explicit reviewed CSV choices, durable source dedupe and atomic intake."""

import json
import secrets
from datetime import UTC, date, datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy.orm import Session

from core.events import get_event_bus
from modules.automation import ExpirationEstimator
from modules.catalog.services.barcodes import find_item_by_name, resolve_local_item
from modules.catalog.services.import_items import (
    create_or_resolve_import_item,
    lock_import_items,
    verify_import_barcode_choice,
)
from modules.catalog.services.items import get_item
from modules.inventory.services.inventory import add_inventory, get_location
from modules.shopping import purchase_review_repository as repository
from modules.shopping.schemas.receipt import ReceiptCommitLine
from modules.shopping.services.purchase_csv_source import parse_source
from modules.shopping.services.receipt_review import WARNING, annotate_lines, fingerprint, source_role

VERSION = "purchase-review.v1"


def create_draft(db, *, actor_id, payload):
    from modules.shopping.services.purchases import compute_purchase_fingerprint

    values = dict(payload)
    location = UUID(str(values["location_id"]))
    if not get_location(db, location):
        raise LookupError("Location not found")
    merchant = (values.get("merchant") or "").strip() or None
    purchased_on = values.get("purchased_on")
    if isinstance(purchased_on, str):
        purchased_on = date.fromisoformat(purchased_on)
    source_fingerprint = compute_purchase_fingerprint(
        csv_text=values["csv_text"], merchant=merchant, purchased_on=purchased_on, location_id=location
    )
    rows = parse_source(values["csv_text"], default_unit=values["default_unit"])
    for line in rows:
        item = resolve_local_item(db, line["barcode"]) if line["barcode"] else None
        exact = bool(item)
        item = item or find_item_by_name(db, line["name"])
        if item and not item.is_active:
            item = None
        financial = source_role(line["name"] + " " + line["source_quantity"])
        if financial:
            line["source_nonstock"] = True
            line["role"] = financial
            line["warnings"].append("Financial/return source: exclude this row")
        line.update(
            line_id=str(uuid4()),
            matched_item_id=str(item.id) if item else None,
            matched_item_name=item.name if item else None,
            matched_item_brand=item.brand if item else None,
            resolution="barcode" if exact and item else "name_or_canonical" if item else "unresolved",
            excluded=False,
            confirmed=False,
            create_new=False,
            annotation=None,
        )
    duplicate = repository.existing_purchase(db, location, source_fingerprint)
    binding = fingerprint(
        {
            "source": source_fingerprint,
            "create_missing_items": values["create_missing_items"],
            "apply_to_inventory": values["apply_to_inventory"],
            "default_unit": values["default_unit"],
        }
    )
    document = {
        "version": VERSION,
        "fingerprint": binding,
        "source_fingerprint": source_fingerprint,
        "csv_text": values["csv_text"],
        "location_id": str(location),
        "merchant": merchant,
        "purchased_on": purchased_on.isoformat() if purchased_on else None,
        "create_missing_items": values["create_missing_items"],
        "apply_to_inventory": values["apply_to_inventory"],
        "default_unit": values["default_unit"],
        "lines": rows,
    }
    token = secrets.token_urlsafe(32)
    row = repository.create_draft(
        db,
        actor_id=actor_id,
        token_hash=fingerprint(token),
        expires_at=datetime.now(UTC) + timedelta(hours=24),
        document=document,
    )
    return {
        **document,
        "draft_id": str(row.id),
        "token": token,
        "duplicate_of_purchase_id": str(duplicate) if duplicate else None,
        "line_count": len(rows),
        "warning": WARNING,
    }


def _check(row, source_fingerprint, lines):
    if row is None:
        raise LookupError("Purchase draft not found")
    expires = row.expires_at.replace(tzinfo=UTC) if row.expires_at.tzinfo is None else row.expires_at
    if expires <= datetime.now(UTC):
        raise ValueError("Purchase draft expired; preview again")
    if row.document["fingerprint"] != source_fingerprint:
        raise ValueError("CSV source or options changed; preview again")
    original = {line["line_id"]: line for line in row.document["lines"]}
    if len(lines) != len(original) or len({line["line_id"] for line in lines}) != len(lines):
        raise ValueError("CSV source rows changed; preview again")
    for line in lines:
        saved = original.get(line["line_id"])
        if not saved or line["raw_line"] != saved["raw_line"] or line.get("barcode") != saved["barcode"]:
            raise ValueError("CSV source row changed; preview again")


def review_draft(db, *, draft_id, actor_id, token, source_fingerprint, lines):
    row = repository.read_draft(db, draft_id, actor_id, fingerprint(token))
    _check(row, source_fingerprint, lines)
    if row.document.get("commit_response"):
        raise ValueError("CSV already committed")
    # Nonstock source rows cannot consume inference merely by editing their role.
    original = {line["line_id"]: line for line in row.document["lines"]}
    eligible = [line for line in lines if not original[line["line_id"]]["source_nonstock"]]
    return {**annotate_lines(db, eligible, source_context="Purchase CSV export row"), "version": VERSION}


def apply_reviewed_rows(db, *, document, lines, deferred_events):
    """Worker session commits remain savepoints owned by caller's transaction."""
    location = UUID(document["location_id"])
    if not get_location(db, location):
        raise LookupError("Location not found")
    commands = [(line, ReceiptCommitLine.model_validate(line)) for line in lines]
    lock_import_items(db, [{"name": command.name, "barcode": line.get("barcode")} for line, command in commands])
    # A conflicting import may have committed while an identity lock was held.
    # Discard cached ORM state and revalidate only after obtaining every lock.
    db.expire_all()
    if not get_location(db, location):
        raise LookupError("Location no longer exists; preview again")
    validated = []
    for line, command in commands:
        item = get_item(db, command.matched_item_id) if command.matched_item_id else None
        if command.matched_item_id and (not item or not item.is_active):
            raise LookupError("Selected item is missing or archived; review again")
        exact = resolve_local_item(db, line["barcode"]) if line.get("barcode") else None
        if exact and (not exact.is_active or command.matched_item_id and exact.id != command.matched_item_id):
            raise ValueError("Exact barcode identity changed or conflicts with selection; preview again")
        if command.create_new and not document["create_missing_items"]:
            raise ValueError("Creating missing items is disabled for this draft")
        if item and item.variable_weight and command.unit == "each":
            raise ValueError("Variable-weight items require their actual package weight")
        validated.append((line, command, item))
    purchase = repository.new_purchase(
        db,
        id=uuid4(),
        merchant=document["merchant"],
        source="csv",
        purchased_on=date.fromisoformat(document["purchased_on"]) if document["purchased_on"] else None,
        location_id=location,
        source_fingerprint=document["source_fingerprint"],
        status="processing",
    )
    estimator = ExpirationEstimator()
    created = []
    transactions = []
    result = []
    resolved_in_batch = {}
    for source, command, item in validated:
        if item is None:
            item, is_new = create_or_resolve_import_item(db, name=command.name, barcode=source.get("barcode"))
            if is_new:
                created.append(item.id)
        verify_import_barcode_choice(db, item=item, barcode=source.get("barcode"), resolved_in_batch=resolved_in_batch)
        if item.variable_weight and command.unit == "each":
            raise ValueError("Variable-weight items require package weight")
        days = item.default_expiration_days or estimator.estimate(item.name, None)
        if document["apply_to_inventory"]:
            tx, _, _ = add_inventory(
                db,
                item_id=item.id,
                location_id=location,
                quantity=command.quantity,
                unit=command.unit,
                expiration_date=(purchase.purchased_on or date.today()) + timedelta(days=int(days)),
                reason="purchase_import",
                note=f"purchase:{purchase.id}",
                deferred_events=deferred_events,
            )
            transactions.append(tx.id)
        line = repository.new_line(
            db,
            purchase_id=purchase.id,
            raw_line=source["raw_line"],
            parsed_name=command.name,
            parsed_quantity=command.quantity,
            parsed_unit=command.unit,
            parsed_barcode=source.get("barcode"),
            matched_item_id=item.id,
            expiration_days_estimate=int(days),
        )
        result.append(
            {
                "line_item_id": line.id,
                "raw_line": line.raw_line,
                "parsed_name": line.parsed_name,
                "parsed_quantity": str(line.parsed_quantity),
                "parsed_unit": line.parsed_unit,
                "parsed_barcode": line.parsed_barcode,
                "matched_item_id": item.id,
                "matched_item_name": item.name,
            }
        )
    repository.complete_purchase(db, purchase, len(result))
    return {
        "purchase_id": purchase.id,
        "status": purchase.status,
        "line_count": len(result),
        "created_item_ids": created,
        "inventory_transaction_ids": transactions,
        "lines": result,
        "created_at": purchase.created_at,
    }


def commit_draft(db, *, draft_id, actor_id, token, source_fingerprint, lines):
    try:
        row = repository.locked_draft(db, draft_id, actor_id, fingerprint(token))
        _check(row, source_fingerprint, lines)
        digest = fingerprint(lines)
        if row.document.get("commit_response"):
            if row.document["commit_hash"] != digest:
                raise ValueError("CSV already committed with different choices")
            result = row.document["commit_response"]
            db.rollback()
            return result
        original = {line["line_id"]: line for line in row.document["lines"]}
        included = []
        for line in lines:
            if line["excluded"]:
                continue
            if not line["confirmed"] or line["role"] != "stock":
                raise ValueError("Verify or exclude every CSV row")
            if original[line["line_id"]]["source_nonstock"]:
                raise ValueError("Return/nonpositive/financial source rows must be excluded")
            included.append(line)
        if not included:
            raise ValueError("No purchase rows selected")
        claim = repository.claim_source(db, UUID(row.document["location_id"]), row.document["source_fingerprint"])
        events = []
        with Session(bind=db.connection(), join_transaction_mode="create_savepoint") as worker:
            result = apply_reviewed_rows(worker, document=row.document, lines=included, deferred_events=events)
        result = json.loads(json.dumps(result, default=str))
        claim.purchase_id = UUID(result["purchase_id"])
        row.document = {**row.document, "commit_hash": digest, "commit_response": result}
        db.commit()
        get_event_bus().publish_all(events, db)
        return result
    except Exception:
        db.rollback()
        raise
