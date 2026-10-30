"""One-time re-expression of the event log into base units (ADR 0016 Phase 1c).

inv_balance is a full fold of the event log, so switching intake to store base
units means historical events must be re-expressed too (you can't reconcile a
delta-based ledger by appending one event). This rewrites each event's
(quantity, unit) into its item's base unit — identity for counted items
(each→each), a real conversion for measured ones (gal→ml, oz→g, 1 jug → its
contents) — and LEAVES events it can't bridge (a size-less item in cups) exactly
as they are, so the balance stays self-consistent. Then it rebuilds projections.

Idempotent: an event already in its base unit is skipped. Prints a per-item
before/after summary for verification.

Run:  python -m scripts.reexpress_base_units          (apply)
      python -m scripts.reexpress_base_units --dry    (report only)
"""

from __future__ import annotations

import sys
from decimal import Decimal

from sqlalchemy import select

from app.db.session import SessionLocal
from app.models import Inventory, Item
from modules.catalog.services.units import (
    base_unit_for,
    convert_quantity,
    is_count_unit,
    net_contents_in_base,
    normalize_unit,
)
from modules.inventory.models import InventoryEvent
from modules.inventory.projector import rebuild_projections


def _dec(v):
    return None if v is None else Decimal(str(v))


def reexpress(item: Item, quantity: Decimal, unit: str) -> tuple[Decimal, str] | None:
    """New (quantity, base_unit) for an event, or None to leave it untouched."""
    base = base_unit_for(item.base_unit, item.stock_mode, item.size_unit)
    if normalize_unit(unit) == normalize_unit(base):
        return None  # already base
    density = _dec(item.density_g_per_ml)
    gpe = _dec(item.grams_per_each)
    if is_count_unit(unit):
        if is_count_unit(base):
            return Decimal(quantity), base  # counted: each/ct → each
        net = net_contents_in_base(
            item.base_unit, item.stock_mode, item.size_quantity, item.size_unit,
            density_g_per_ml=density, grams_per_each=gpe,
        )
        return (Decimal(quantity) * net, base) if net is not None else None
    conv = convert_quantity(Decimal(quantity), unit, base, density_g_per_ml=density, grams_per_each=gpe)
    return (conv, base) if conv is not None else None


def main() -> None:
    dry = "--dry" in sys.argv
    db = SessionLocal()
    items = {i.id: i for i in db.execute(select(Item)).scalars().all()}
    events = db.execute(select(InventoryEvent).order_by(InventoryEvent.seq)).scalars().all()

    changed = 0
    left = 0
    touched_items: set = set()
    for ev in events:
        item = items.get(ev.item_id)
        if item is None:
            continue
        result = reexpress(item, Decimal(ev.quantity), ev.unit)
        if result is None:
            # Already base, or no bridge → leave as-is.
            if normalize_unit(ev.unit) != normalize_unit(base_unit_for(item.base_unit, item.stock_mode, item.size_unit)):
                left += 1
            continue
        new_qty, new_unit = result
        if not dry:
            ev.quantity = new_qty
            ev.unit = new_unit
        changed += 1
        touched_items.add(item.id)

    # The legacy `inventory` table is maintained imperatively by add/remove and read
    # for the stock unit on removal, so it must be re-expressed too — else it stays
    # stale (1 each / 1 gal) while the projection is base (499 g / 3785 ml).
    rows_changed = 0
    for row in db.execute(select(Inventory)).scalars().all():
        item = items.get(row.item_id)
        if item is None:
            continue
        result = reexpress(item, Decimal(row.quantity), row.unit)
        if result is None:
            continue
        new_qty, new_unit = result
        if not dry:
            row.quantity = new_qty
            row.unit = new_unit
        rows_changed += 1

    if not dry:
        db.commit()
        rebuild_projections(db)
        db.commit()

    print(
        f"events: {len(events)} | rewritten: {changed} | left (no bridge): {left} "
        f"| legacy rows rewritten: {rows_changed} | dry={dry}"
    )
    for iid in touched_items:
        it = items[iid]
        base = base_unit_for(it.base_unit, it.stock_mode, it.size_unit)
        print(f"  {it.name[:48]:48} → base {base}")
    db.close()


if __name__ == "__main__":
    main()
