from __future__ import annotations

import csv
import hashlib
import io
import uuid
from datetime import date, timedelta
from decimal import Decimal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Item, Purchase, PurchaseLineItem
from modules.automation import ExpirationEstimator
from modules.catalog.services.barcodes import find_item_by_name, normalize_barcode, resolve_local_item
from modules.catalog.services.import_items import (
    create_or_resolve_import_item,
    lock_import_items,
    verify_import_barcode_choice,
)
from modules.inventory.services.inventory import add_inventory, get_location
from modules.shopping import purchase_review_repository as review_repository


def _parse_decimal(value: str | None, *, default: Decimal) -> Decimal:
    raw = (value or "").strip()
    if not raw:
        return default
    try:
        parsed = Decimal(raw)
    except Exception:
        return default
    if parsed <= 0:
        return default
    return parsed


def _normalize_header(value: str) -> str:
    return "".join(ch for ch in (value or "").strip().lower() if ch.isalnum() or ch in {"_", " "}).replace(" ", "_")


def parse_purchase_csv(csv_text: str, *, default_unit: str = "each") -> list[dict]:
    """
    Best-effort CSV parser for purchase history exports.

    Expected columns (flexible):
    - name/item/product/description
    - quantity/qty
    - unit/uom
    - barcode/upc/ean

    If no headers are present, assumes first 3 columns are: name, quantity, unit.
    """
    text = (csv_text or "").strip()
    if not text:
        return []

    sample = text[:4096]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",\t;|")
    except Exception:
        dialect = csv.excel

    f = io.StringIO(text)
    reader = csv.reader(f, dialect=dialect)
    rows = list(reader)
    if not rows:
        return []

    # Drop fully-empty rows.
    rows = [[cell.strip() for cell in row] for row in rows if any((cell or "").strip() for cell in row)]
    if not rows:
        return []

    first = rows[0]
    normalized = [_normalize_header(col) for col in first]
    has_header = any(
        col in {"name", "item", "product", "description", "quantity", "qty", "unit", "uom", "barcode", "upc", "ean"}
        for col in normalized
    )

    parsed: list[dict] = []

    if has_header:
        header = normalized
        dict_rows = []
        for row in rows[1:]:
            d: dict[str, str] = {}
            for idx, cell in enumerate(row):
                if idx >= len(header):
                    continue
                key = header[idx]
                if not key:
                    continue
                d[key] = (cell or "").strip()
            dict_rows.append(d)

        def get_any(d: dict[str, str], keys: set[str]) -> str | None:
            for k in keys:
                if v := d.get(k):
                    return v
            return None

        for d in dict_rows:
            name = get_any(d, {"name", "item", "product", "description"})
            if not name:
                continue
            qty = _parse_decimal(get_any(d, {"quantity", "qty"}), default=Decimal("1"))
            unit = (get_any(d, {"unit", "uom"}) or default_unit).strip() or default_unit
            barcode = get_any(d, {"barcode", "upc", "ean"})
            parsed.append(
                {
                    "raw_line": ",".join([name, str(qty), unit] + ([barcode] if barcode else [])),
                    "name": name.strip(),
                    "quantity": qty,
                    "unit": unit,
                    "barcode": barcode.strip() if barcode else None,
                }
            )

        return parsed

    # No header: assume columns are name, qty, unit, barcode? (barcode optional).
    for row in rows:
        if not row:
            continue
        name = (row[0] if len(row) > 0 else "").strip()
        if not name:
            continue
        qty = _parse_decimal(row[1] if len(row) > 1 else None, default=Decimal("1"))
        unit = ((row[2] if len(row) > 2 else "") or default_unit).strip() or default_unit
        barcode = (row[3] if len(row) > 3 else "").strip() or None
        parsed.append(
            {
                "raw_line": ",".join([c for c in [name, str(qty), unit, barcode] if c]),
                "name": name,
                "quantity": qty,
                "unit": unit,
                "barcode": barcode,
            }
        )

    return parsed


def compute_purchase_fingerprint(
    *,
    csv_text: str,
    merchant: str | None,
    purchased_on: date | None,
    location_id: UUID,
) -> str:
    # Best-effort idempotency key (local-first). This is not intended as a security feature.
    normalized_csv = "\n".join(line.rstrip() for line in (csv_text or "").strip().splitlines())
    meta = f"merchant={merchant or ''}\npurchased_on={purchased_on.isoformat() if purchased_on else ''}\nlocation_id={location_id}\n"
    return hashlib.sha256((meta + normalized_csv).encode("utf-8")).hexdigest()


def _import_purchase_csv_text_impl(
    db: Session,
    *,
    csv_text: str,
    location_id: UUID,
    merchant: str | None,
    purchased_on: date | None,
    create_missing_items: bool,
    apply_to_inventory: bool,
    default_unit: str,
    deferred_events=None,
    parsed_rows=None,
) -> dict:
    location = get_location(db, location_id)
    if not location:
        raise LookupError("Location not found")

    normalized_merchant = merchant.strip() if merchant else None
    fingerprint = compute_purchase_fingerprint(
        csv_text=csv_text,
        merchant=normalized_merchant,
        purchased_on=purchased_on,
        location_id=location_id,
    )
    existing_id = review_repository.existing_purchase(db, location_id, fingerprint)
    if existing_id:
        raise ValueError(f"Duplicate import (purchase_id={existing_id})")

    purchase = Purchase(
        merchant=normalized_merchant,
        source="csv",
        purchased_on=purchased_on,
        location_id=location_id,
        source_fingerprint=fingerprint,
        status="processing",
    )
    db.add(purchase)
    db.commit()
    db.refresh(purchase)

    estimator = ExpirationEstimator()
    parsed_lines = parsed_rows if parsed_rows is not None else parse_purchase_csv(csv_text, default_unit=default_unit)
    lock_import_items(db, parsed_lines)

    tx_ids: list[UUID] = []
    created_item_ids: list[UUID] = []
    response_lines: list[dict] = []
    resolved_in_batch = {}

    base_date = purchased_on or date.today()

    for parsed in parsed_lines:
        barcode = normalize_barcode(parsed.get("barcode") or "") if parsed.get("barcode") else None

        item = None
        if barcode:
            item = resolve_local_item(db, barcode)
        if not item:
            item = find_item_by_name(db, parsed["name"])

        if item is not None and not item.is_active:
            raise LookupError("Matching item is archived; choose an active item in reviewed CSV")

        if not item:
            if not create_missing_items:
                matched_item_id = None
                matched_item_name = None
                estimated_days = estimator.estimate(parsed["name"], None)
            else:
                estimated_days = estimator.estimate(parsed["name"], None)
                item, created = create_or_resolve_import_item(db, name=parsed["name"].strip(), barcode=barcode)
                if created:
                    created_item_ids.append(item.id)
                matched_item_id = item.id
                matched_item_name = item.name
        else:
            matched_item_id = item.id
            matched_item_name = item.name
            estimated_days = item.default_expiration_days or estimator.estimate(item.name, None)

        if item is not None:
            verify_import_barcode_choice(db, item=item, barcode=barcode, resolved_in_batch=resolved_in_batch)
        expiration_date = base_date + timedelta(days=int(estimated_days))

        if apply_to_inventory and item is not None:
            tx, _row, _item = add_inventory(
                db,
                item_id=item.id,
                location_id=location_id,
                quantity=Decimal(parsed["quantity"]),
                unit=parsed["unit"],
                expiration_date=expiration_date,
                reason="purchase_import",
                note=f"purchase:{purchase.id}",
                deferred_events=deferred_events,
            )
            tx_ids.append(tx.id)

        line = PurchaseLineItem(
            purchase_id=purchase.id,
            raw_line=parsed["raw_line"],
            parsed_name=parsed["name"],
            parsed_quantity=Decimal(parsed["quantity"]),
            parsed_unit=parsed["unit"],
            parsed_barcode=barcode,
            matched_item_id=item.id if item else None,
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
                "parsed_barcode": line.parsed_barcode,
                "matched_item_id": matched_item_id,
                "matched_item_name": matched_item_name,
            }
        )

    purchase.status = "processed"
    purchase.line_count = len(response_lines)
    db.add(purchase)
    db.commit()
    db.refresh(purchase)

    return {
        "purchase_id": purchase.id,
        "status": purchase.status,
        "line_count": len(response_lines),
        "created_item_ids": created_item_ids,
        "inventory_transaction_ids": tx_ids,
        "lines": response_lines,
        "created_at": purchase.created_at,
    }


def import_purchase_csv_text(db: Session, **kwargs) -> dict:
    """Legacy wire contract with the same atomic source claim as reviewed CSV."""
    from core.events import get_event_bus
    from modules.shopping import purchase_review_repository as review_repository
    from modules.shopping.services.purchase_csv_source import parse_source
    from modules.shopping.services.receipt_review import source_role

    parsed = parse_source(kwargs["csv_text"], default_unit=kwargs["default_unit"])
    for line in parsed:
        if not line["name"].strip():
            raise ValueError("Missing CSV product name; use reviewed preview to correct or exclude the row")
        try:
            quantity = Decimal(line["source_quantity"])
        except Exception as exc:
            raise ValueError("Invalid CSV quantity; use the reviewed preview to correct it") from exc
        if not quantity.is_finite() or quantity <= 0 or source_role(line["name"] + " " + line["source_quantity"]):
            raise ValueError("Return, financial or nonpositive rows require reviewed exclusion")
    if not get_location(db, kwargs["location_id"]):
        raise LookupError("Location not found")
    source = compute_purchase_fingerprint(
        csv_text=kwargs["csv_text"],
        location_id=kwargs["location_id"],
        merchant=(kwargs["merchant"] or "").strip() or None,
        purchased_on=kwargs["purchased_on"],
    )
    events = []
    try:
        # SQLite also needs a physical outer write transaction before savepoints.
        claim = review_repository.claim_source(db, kwargs["location_id"], source)
        with Session(bind=db.connection(), join_transaction_mode="create_savepoint") as worker:
            result = _import_purchase_csv_text_impl(
                worker,
                **kwargs,
                deferred_events=events,
                parsed_rows=[{**line, "quantity": Decimal(line["source_quantity"])} for line in parsed],
            )
        claim.purchase_id = result["purchase_id"]
        db.commit()
        get_event_bus().publish_all(events, db)
        return result
    except Exception:
        db.rollback()
        raise


def list_purchases(db: Session, *, limit: int = 50) -> list[Purchase]:
    capped = max(1, min(int(limit), 200))
    return list(db.execute(select(Purchase).order_by(Purchase.created_at.desc()).limit(capped)).scalars().all())


def get_purchase(db: Session, purchase_id: UUID) -> Purchase | None:
    return db.execute(select(Purchase).where(Purchase.id == purchase_id)).scalar_one_or_none()


def list_purchase_lines(db: Session, purchase_id: UUID) -> list[dict]:
    rows = db.execute(
        select(PurchaseLineItem, Item)
        .join(Item, PurchaseLineItem.matched_item_id == Item.id, isouter=True)
        .where(PurchaseLineItem.purchase_id == purchase_id)
        .order_by(PurchaseLineItem.created_at.asc())
    ).all()

    out: list[dict] = []
    for line, item in rows:
        out.append(
            {
                "line_item_id": line.id,
                "raw_line": line.raw_line,
                "parsed_name": line.parsed_name,
                "parsed_quantity": str(line.parsed_quantity),
                "parsed_unit": line.parsed_unit,
                "parsed_barcode": line.parsed_barcode,
                "matched_item_id": line.matched_item_id,
                "matched_item_name": item.name if item else None,
            }
        )
    return out


def preview_purchase_csv_text(
    db: Session,
    *,
    csv_text: str,
    location_id: UUID,
    merchant: str | None,
    purchased_on: date | None,
    create_missing_items: bool,
    apply_to_inventory: bool,
    default_unit: str,
) -> dict:
    location = get_location(db, location_id)
    if not location:
        raise LookupError("Location not found")

    normalized_merchant = merchant.strip() if merchant else None
    fingerprint = compute_purchase_fingerprint(
        csv_text=csv_text,
        merchant=normalized_merchant,
        purchased_on=purchased_on,
        location_id=location_id,
    )

    duplicate_of_purchase_id = review_repository.existing_purchase(db, location_id, fingerprint)

    from modules.shopping.services.purchase_csv_source import parse_source

    parsed_lines = parse_source(csv_text, default_unit=default_unit)
    response_lines: list[dict] = []
    would_create_names: list[str] = []

    for parsed in parsed_lines:
        barcode = normalize_barcode(parsed.get("barcode") or "") if parsed.get("barcode") else None

        item = None
        if barcode:
            item = resolve_local_item(db, barcode)
        if not item:
            item = find_item_by_name(db, parsed["name"])

        would_create_item = False
        matched_item_id = None
        matched_item_name = None

        if item:
            matched_item_id = item.id
            matched_item_name = item.name
        elif create_missing_items:
            would_create_item = True
            matched_item_name = parsed["name"].strip()
            if matched_item_name and matched_item_name not in would_create_names:
                would_create_names.append(matched_item_name)

        would_add_to_inventory = bool(
            apply_to_inventory
            and parsed["role"] == "stock"
            and not parsed["source_nonstock"]
            and (item is not None or would_create_item)
        )

        response_lines.append(
            {
                "line_item_id": uuid.uuid4(),
                "raw_line": parsed["raw_line"],
                "parsed_name": parsed["name"],
                "parsed_quantity": str(parsed["quantity"]),
                "parsed_unit": parsed["unit"],
                "parsed_barcode": barcode,
                "matched_item_id": matched_item_id,
                "matched_item_name": matched_item_name,
                "would_create_item": would_create_item,
                "would_add_to_inventory": would_add_to_inventory,
            }
        )

    return {
        "fingerprint": fingerprint,
        "duplicate_of_purchase_id": duplicate_of_purchase_id,
        "line_count": len(response_lines),
        "would_create_item_names": would_create_names,
        "lines": response_lines,
    }
