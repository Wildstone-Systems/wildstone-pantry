"""Resolve a product line (name and/or barcode) to a catalog item reference.

The catalog's public seam for other modules (e.g. the agent API): callers get
plain dicts, never ``Item`` rows, so catalog persistence stays catalog-owned.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.models import Item
from modules.catalog.schemas.item import ItemCreate
from modules.catalog.services.barcodes import find_item_by_name, resolve_local_item
from modules.catalog.services.items import create_item

MATCH_BARCODE = "barcode"
MATCH_NAME = "existing"
MATCH_CREATED = "created"


def _ref(item: Item, match: str) -> dict:
    return {
        "item_id": item.id,
        "item_name": item.name,
        "match": match,
        "category": item.category.name if item.category is not None else None,
        "default_expiration_days": item.default_expiration_days,
    }


def find_item_ref(db: Session, *, name: str, barcode: str | None = None) -> dict | None:
    """Local barcode first (no network), then the brand/size-insensitive name match."""
    if barcode:
        item = resolve_local_item(db, barcode)
        if item is not None:
            return _ref(item, MATCH_BARCODE)
    if name:
        item = find_item_by_name(db, name)
        if item is not None:
            return _ref(item, MATCH_NAME)
    return None


def create_item_ref(db: Session, *, name: str, barcode: str | None = None, brand: str | None = None) -> dict:
    item = create_item(db, ItemCreate(name=name, brand=brand, barcode=barcode, source="agent"))
    return _ref(item, MATCH_CREATED)
