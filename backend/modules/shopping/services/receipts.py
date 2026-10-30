from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from uuid import UUID

from sqlalchemy.orm import Session

from app.models import Item, Receipt, ReceiptLineItem
from modules.automation import ExpirationEstimator, parse_receipt_text
from modules.catalog.schemas import ItemCreate
from modules.catalog.services.barcodes import find_item_by_name
from modules.catalog.services.items import create_item, get_item
from modules.inventory.services.inventory import add_inventory, get_location
from modules.shopping.schemas.receipt import ReceiptCommitLine


def preview_receipt_text(
    db: Session,
    *,
    raw_text: str,
    location_id: UUID,
    source_filename: str | None = None,
) -> dict:
    """Parse receipt text without writing to DB. Returns preview with matches."""
    location = get_location(db, location_id)
    if not location:
        raise LookupError("Location not found")

    estimator = ExpirationEstimator()
    parsed_lines = parse_receipt_text(raw_text)

    preview_lines: list[dict] = []

    for parsed in parsed_lines:
        item = find_item_by_name(db, parsed["name"])
        if item:
            estimated_days = item.default_expiration_days or estimator.estimate(item.name, None)
        else:
            estimated_days = estimator.estimate(parsed["name"], None)

        preview_lines.append(
            {
                "raw_line": parsed["raw_line"],
                "name": parsed["name"],
                "quantity": str(parsed["quantity"]),
                "unit": parsed["unit"],
                "matched_item_id": item.id if item else None,
                "matched_item_name": item.name if item else None,
                "expiration_days_estimate": int(estimated_days),
            }
        )

    return {
        "lines": preview_lines,
        "line_count": len(preview_lines),
    }


def preview_receipt_image(
    db: Session,
    *,
    image: bytes,
    media_type: str,
    location_id: UUID,
    source_filename: str | None = None,
) -> dict:
    """Receipt photo -> draft preview lines: rules first, AI fallback (ADR 0018 §9).

    The deterministic path (OCR + regex parser) runs first. When OCR is
    unavailable or the parser finds nothing, a configured AI provider takes
    over — its lines carry ``source="ai"`` + confidence, and like every
    preview they are drafts: only the human commit writes to the ledger.
    Raises AIUnavailable-as-RuntimeError semantics via LookupError/RuntimeError
    for the route to map.
    """
    from modules.automation import image_bytes_to_text
    from modules.automation.providers import CAP_VISION, AIUnavailable, resolve_provider

    location = get_location(db, location_id)
    if not location:
        raise LookupError("Location not found")

    rules_error: str | None = None
    try:
        text = image_bytes_to_text(image)
        preview = preview_receipt_text(
            db, raw_text=text, location_id=location_id, source_filename=source_filename
        )
        if preview["line_count"] > 0:
            preview["engine"] = "rules"
            preview["extracted_text"] = text
            return preview
        rules_error = "The deterministic parser found no line items."
    except RuntimeError as exc:  # OCR dependencies missing
        rules_error = str(exc)

    provider = resolve_provider(db, CAP_VISION)
    if provider is None:
        raise RuntimeError(
            f"{rules_error} No AI provider is configured to take over "
            "(Admin → Integrations → AI)."
        )
    try:
        extraction = provider.extract_receipt(db, image, media_type=media_type)
    except AIUnavailable as exc:
        raise RuntimeError(f"{rules_error} AI fallback failed: {exc}") from exc

    estimator = ExpirationEstimator()
    preview_lines: list[dict] = []
    for line in extraction.lines:
        item = find_item_by_name(db, line.name)
        estimated = (
            item.default_expiration_days or estimator.estimate(item.name, None)
            if item
            else estimator.estimate(line.name, None)
        )
        preview_lines.append(
            {
                "raw_line": line.raw_text or line.name,
                "name": line.name,
                "quantity": line.quantity,
                "unit": line.unit,
                "matched_item_id": item.id if item else None,
                "matched_item_name": item.name if item else None,
                "expiration_days_estimate": int(estimated),
                "source": line.source,
                "confidence": line.confidence,
            }
        )
    return {"lines": preview_lines, "line_count": len(preview_lines), "engine": extraction.provider}


def commit_receipt_lines(
    db: Session,
    *,
    location_id: UUID,
    source_filename: str | None,
    raw_text: str | None,
    lines: list[dict],
    deferred_events: list | None = None,
) -> dict:
    """Commit reviewed receipt lines to inventory."""
    location = get_location(db, location_id)
    if not location:
        raise LookupError("Location not found")

    # Validate the whole reviewed batch before any writes, including stale IDs.
    # Never turn a rejected selection into an implicit catalog creation.
    reviewed = [ReceiptCommitLine.model_validate(line) for line in lines]
    selected_items = []
    for line in reviewed:
        item = get_item(db, line.matched_item_id) if line.matched_item_id else None
        if line.matched_item_id and (item is None or not item.is_active):
            raise LookupError("Selected item not found or inactive; review the receipt again")
        if item and item.variable_weight and line.unit == "each":
            raise ValueError(f"{item.name} requires the actual package weight instead of each")
        selected_items.append(item)

    receipt = Receipt(source_filename=source_filename, raw_text=raw_text, status="processing")
    db.add(receipt)
    db.commit()
    db.refresh(receipt)

    estimator = ExpirationEstimator()

    tx_ids: list[UUID] = []
    response_lines: list[dict] = []

    for reviewed_line, item in zip(reviewed, selected_items):
        line = reviewed_line.model_dump()
        if not item:
            estimated_days = estimator.estimate(line["name"], None)
            item = create_item(db, ItemCreate(name=line["name"], default_expiration_days=estimated_days))
        else:
            estimated_days = item.default_expiration_days or estimator.estimate(item.name, None)

        expiration_date = date.today() + timedelta(days=int(estimated_days))

        qty = line["quantity"]
        unit = line["unit"]

        tx, _row, _item = add_inventory(
            db,
            item_id=item.id,
            location_id=location_id,
            quantity=qty,
            unit=unit,
            expiration_date=expiration_date,
            reason="receipt_import",
            note=f"receipt:{receipt.id}",
            deferred_events=deferred_events,
        )
        tx_ids.append(tx.id)

        line_item = ReceiptLineItem(
            receipt_id=receipt.id,
            raw_line=line["raw_line"],
            parsed_name=line["name"],
            parsed_quantity=qty,
            parsed_unit=unit,
            matched_item_id=item.id,
            expiration_days_estimate=int(estimated_days),
        )
        db.add(line_item)
        db.commit()
        db.refresh(line_item)

        response_lines.append(
            {
                "line_item_id": line_item.id,
                "raw_line": line_item.raw_line,
                "parsed_name": line_item.parsed_name,
                "parsed_quantity": str(line_item.parsed_quantity),
                "parsed_unit": line_item.parsed_unit,
                "matched_item_id": line_item.matched_item_id,
                "matched_item_name": item.name,
            }
        )

    receipt.status = "processed"
    db.add(receipt)
    db.commit()
    db.refresh(receipt)

    return {
        "receipt_id": receipt.id,
        "status": receipt.status,
        "line_count": len(response_lines),
        "inventory_transaction_ids": tx_ids,
        "lines": response_lines,
        "created_at": receipt.created_at,
    }


def ingest_receipt_text(
    db: Session,
    *,
    raw_text: str,
    location_id: UUID,
    source_filename: str | None,
) -> dict:
    location = get_location(db, location_id)
    if not location:
        raise LookupError("Location not found")

    receipt = Receipt(source_filename=source_filename, raw_text=raw_text, status="processing")
    db.add(receipt)
    db.commit()
    db.refresh(receipt)

    estimator = ExpirationEstimator()
    parsed_lines = parse_receipt_text(raw_text)

    tx_ids: list[UUID] = []
    response_lines: list[dict] = []

    for parsed in parsed_lines:
        item = find_item_by_name(db, parsed["name"])
        if not item:
            estimated_days = estimator.estimate(parsed["name"], None)
            item = Item(name=parsed["name"], default_expiration_days=estimated_days)
            db.add(item)
            db.commit()
            db.refresh(item)
        else:
            estimated_days = item.default_expiration_days or estimator.estimate(item.name, None)

        expiration_date = date.today() + timedelta(days=int(estimated_days))

        tx, _row, _item = add_inventory(
            db,
            item_id=item.id,
            location_id=location_id,
            quantity=Decimal(parsed["quantity"]),
            unit=parsed["unit"],
            expiration_date=expiration_date,
            reason="receipt_import",
            note=f"receipt:{receipt.id}",
        )
        tx_ids.append(tx.id)

        line = ReceiptLineItem(
            receipt_id=receipt.id,
            raw_line=parsed["raw_line"],
            parsed_name=parsed["name"],
            parsed_quantity=Decimal(parsed["quantity"]),
            parsed_unit=parsed["unit"],
            matched_item_id=item.id,
            expiration_days_estimate=int(estimated_days),
        )
        db.add(line)
        db.commit()
        db.refresh(line)

        response_lines.append(
            {
                "line_item_id": line.id,
                "raw_line": line.raw_line,
                "parsed_name": line.parsed_name,
                "parsed_quantity": str(line.parsed_quantity),
                "parsed_unit": line.parsed_unit,
                "matched_item_id": line.matched_item_id,
                "matched_item_name": item.name,
            }
        )

    receipt.status = "processed"
    db.add(receipt)
    db.commit()
    db.refresh(receipt)

    return {
        "receipt_id": receipt.id,
        "status": receipt.status,
        "line_count": len(response_lines),
        "inventory_transaction_ids": tx_ids,
        "lines": response_lines,
        "created_at": receipt.created_at,
    }
