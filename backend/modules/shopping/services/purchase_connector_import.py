"""Commit connector orders into inventory (step 5b — the intake side).

``import_orders`` is the network-free core: given already-fetched
``NormalizedOrder``s and a location, it matches/creates items and appends
``purchased`` intake events via the shared ``add_inventory`` path (ADR 0002), so
IMAP purchases land on the same ledger as manual and CSV intake. Idempotent by
each order's ``external_id`` (the email Message-ID) — re-importing is skipped.

``commit_connector`` wraps it with the networked ``fetch_orders()`` step; a
dormant connector raises ``NotConfiguredError`` before any write.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import InventoryTransaction, Item
from modules.automation import ExpirationEstimator
from modules.catalog.services.barcodes import find_item_by_name
from modules.inventory.services.inventory import add_inventory, get_location
from modules.shopping.connectors import NormalizedOrder, get_connector
from modules.shopping.services.connector_config import get_effective

_NOTE_PREFIX = "imap"


@dataclass
class OrderImportResult:
    orders: int = 0
    imported_orders: int = 0
    skipped_orders: int = 0  # already imported (dedup on external_id)
    imported_lines: int = 0
    transaction_ids: list[str] = field(default_factory=list)


def _note(external_id: str | None, vendor: str) -> str:
    return f"{_NOTE_PREFIX}:{external_id or ''}:{vendor}"


def _already_imported(db: Session, external_id: str) -> bool:
    marker = f"{_NOTE_PREFIX}:{external_id}:"
    row = db.execute(
        select(InventoryTransaction.id).where(InventoryTransaction.note.like(f"{marker}%")).limit(1)
    ).first()
    return row is not None


def import_orders(
    db: Session,
    orders: list[NormalizedOrder],
    *,
    location_id: UUID,
    actor: str | None = None,
) -> OrderImportResult:
    """Import fetched orders into inventory at ``location_id`` (no network).

    Orders carrying an ``external_id`` already present in the ledger are skipped
    so a repeated import doesn't double-count. Raises ``LookupError`` if the
    location doesn't exist.
    """
    if get_location(db, location_id) is None:
        raise LookupError("Location not found")

    estimator = ExpirationEstimator()
    result = OrderImportResult(orders=len(orders))

    for order in orders:
        if order.external_id and _already_imported(db, order.external_id):
            result.skipped_orders += 1
            continue

        lines_in_order = 0
        base_date = order.purchased_on or date.today()
        for line in order.lines:
            name = (line.name or "").strip()
            if len(name) < 2:
                continue

            item = find_item_by_name(db, name)
            if item is None:
                estimated_days = estimator.estimate(name, None)
                item = Item(name=name, default_expiration_days=estimated_days)
                db.add(item)
                db.commit()
                db.refresh(item)
            else:
                estimated_days = item.default_expiration_days or estimator.estimate(item.name, None)

            expiration_date = base_date + timedelta(days=int(estimated_days))
            qty = line.quantity if line.quantity and line.quantity > 0 else Decimal("1")

            try:
                tx, _row, _item = add_inventory(
                    db,
                    item_id=item.id,
                    location_id=location_id,
                    quantity=qty,
                    unit=line.unit or "each",
                    expiration_date=expiration_date,
                    reason="imap_purchase_import",
                    note=_note(order.external_id, order.vendor),
                    actor=actor,
                )
            except Exception:
                continue

            result.transaction_ids.append(str(tx.id))
            result.imported_lines += 1
            lines_in_order += 1

        if lines_in_order:
            result.imported_orders += 1

    return result


def commit_connector(
    db: Session,
    *,
    connector_key: str,
    location_id: UUID,
    actor: str | None = None,
) -> OrderImportResult:
    """Fetch a connector's orders and import them into inventory.

    Raises ``LookupError`` for an unknown connector or missing location, and
    ``NotConfiguredError`` (from ``fetch_orders``) when the connector is dormant.
    """
    connector = get_connector(connector_key)
    if connector is None:
        raise LookupError(f"Unknown purchase connector: {connector_key}")

    config = get_effective(db, connector_key)
    orders = connector.fetch_orders(config)
    return import_orders(db, orders, location_id=location_id, actor=actor)
