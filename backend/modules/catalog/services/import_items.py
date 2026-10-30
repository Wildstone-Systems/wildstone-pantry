"""Public catalog boundary for explicit new-item choices during reviewed intake."""

import hashlib

from sqlalchemy import text

from modules.catalog.schemas import ItemCreate
from modules.catalog.services.barcodes import barcode_variants, find_item_by_name, normalize_barcode, resolve_local_item
from modules.catalog.services.canonical import canonical_ingredient
from modules.catalog.services.items import create_item


def lock_import_items(db, rows):
    """Acquire all identity locks in one stable order before catalog creation."""
    keys = set()
    for row in rows:
        keys.add("name:" + (canonical_ingredient(row["name"]) or row["name"].casefold().strip()))
        if row.get("barcode"):
            # Same equivalence set as local GTIN/alias resolution (UPC/EAN).
            keys.add("barcode:" + min(barcode_variants(row["barcode"])))
    if db.get_bind().dialect.name == "postgresql":
        # Stable transaction-scoped identity lock; no cross-module ORM access.
        for key in sorted(keys):
            lock = int.from_bytes(hashlib.sha256(key.encode()).digest()[:8], "big", signed=True)
            db.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": lock})


def create_or_resolve_import_item(db, *, name, barcode=None):
    normalized = normalize_barcode(barcode) if barcode else None
    lock_import_items(db, [{"name": name, "barcode": barcode}])
    exact = resolve_local_item(db, normalized) if normalized else None
    existing = exact or find_item_by_name(db, name)
    if existing and not existing.is_active:
        raise ValueError("Matching item is archived; select an active catalog item")
    if existing and (
        exact or not normalized or not existing.barcode or normalize_barcode(existing.barcode) == normalized
    ):
        return existing, False
    return create_item(db, ItemCreate(name=name, barcode=normalized)), True


def verify_import_barcode_choice(db, *, item, barcode, resolved_in_batch):
    """Keep each included GTIN bound to one final item, without learning aliases."""
    if not barcode:
        return
    # Earlier rows can create a barcode after the batch's initial validation.
    exact = resolve_local_item(db, barcode)
    if exact and (not exact.is_active or exact.id != item.id):
        raise ValueError("Exact barcode conflicts with the resolved item; review the CSV again")
    key = min(barcode_variants(barcode))
    prior = resolved_in_batch.get(key)
    if prior is not None and prior != item.id:
        raise ValueError("The same barcode has conflicting item choices in this CSV; review again")
    resolved_in_batch[key] = item.id
