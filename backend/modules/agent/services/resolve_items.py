"""Resolve free-text product lines (e.g. from a receipt an agent read) to
catalog items, so the agent can call ``add_inventory`` with real item ids.

Matching order per line: local barcode (no network), then name via
``find_item_by_name`` (brand/size-insensitive canonical match). Unmatched
lines are created only when ``create_missing`` is set — the default is a dry
run that writes nothing. Never touches stock; that stays in ``add_inventory``.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.models import Item
from modules.automation import ExpirationEstimator
from modules.catalog.schemas.item import ItemCreate
from modules.catalog.services.barcodes import find_item_by_name, resolve_local_item
from modules.catalog.services.items import create_item

MATCH_BARCODE = "barcode"
MATCH_NAME = "existing"
MATCH_CREATED = "created"
MATCH_NONE = "not_found"


def _expiry_days(estimator: ExpirationEstimator, item: Item | None, name: str) -> int:
    category = item.category.name if item is not None and item.category is not None else None
    return estimator.estimate(item.name if item is not None else name, category)


def resolve_items(db: Session, *, lines: list[dict], create_missing: bool = False) -> list[dict]:
    """One result per input line, in the same order.

    Lines repeated in one call (same name/barcode) resolve to the same item, so
    a dry run never reports a product twice as "would be created" and a real
    run never creates it twice."""
    estimator = ExpirationEstimator()
    created: dict[str, Item] = {}
    results: list[dict] = []
    for line in lines:
        name = (line.get("name") or "").strip()
        barcode = (line.get("barcode") or "").strip() or None

        item: Item | None = None
        match = MATCH_NONE
        if barcode:
            item = resolve_local_item(db, barcode)
            if item is not None:
                match = MATCH_BARCODE
        if item is None and name:
            item = find_item_by_name(db, name)
            if item is not None:
                match = MATCH_NAME
        key = (barcode or "") + "|" + name.lower()
        if item is None and key in created:
            item, match = created[key], MATCH_CREATED
        if item is None and create_missing and name:
            item = create_item(db, ItemCreate(name=name, brand=line.get("brand"), barcode=barcode, source="agent"))
            created[key] = item
            match = MATCH_CREATED

        results.append(
            {
                "name": name,
                "barcode": barcode,
                "match": match,
                "item_id": item.id if item is not None else None,
                "item_name": item.name if item is not None else None,
                "suggested_expiry_days": _expiry_days(estimator, item, name),
            }
        )
    return results
