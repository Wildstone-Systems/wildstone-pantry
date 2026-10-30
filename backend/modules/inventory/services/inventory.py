from __future__ import annotations

import uuid as uuid_module
from datetime import UTC, date, datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal
from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.models import Inventory, InventoryLocation, InventoryTransaction, Item
from core.events import get_event_bus
from core.events.base import InventoryAdded, InventoryAdjusted, InventoryMoved, InventoryRemoved
from modules.catalog.services.ingredient_reference import resolve_hints
from modules.catalog.services.units import (
    base_unit_for,
    convert_quantity,
    default_base_unit,
    grams_per_each_from_size,
    is_count_unit,
    ml_per_each_from_size,
    net_contents_in_base,
    normalize_unit,
)
from modules.inventory import repository
from modules.inventory.models import InventoryBalance, InventoryEvent, InventoryLot
from modules.inventory.projector import rebuild_projections

# ADR 0002 step 1 (make the ledger real): every inventory mutation now appends an
# immutable ``InventoryEvent`` and rebuilds the projections (``inv_balance`` /
# ``inv_lot``) in the same transaction. During this transition the legacy
# ``inventory`` / ``inventory_transactions`` tables are still written in sync
# (dual-write) so nothing breaks and a parity test can assert they match; reads
# come from the projections. The legacy tables are dropped in a follow-up once
# parity has held. Lots/expiry stay optional — an intake with neither still
# records a plain quantity event.


def _soonest_lot_expiry(db: Session, item_id: UUID, location_id: UUID) -> date | None:
    """FEFO next-to-expire lot expiry for a balance line (None if no dated lot)."""
    return db.execute(
        select(InventoryLot.expiration_date)
        .where(
            InventoryLot.item_id == item_id,
            InventoryLot.location_id == location_id,
            InventoryLot.quantity_remaining > 0,
            InventoryLot.expiration_date.is_not(None),
        )
        .order_by(InventoryLot.expiration_date.asc())
        .limit(1)
    ).scalar_one_or_none()


def list_item_stock_fefo(db: Session, item_id: UUID) -> list[dict]:
    """
    Where an item is in stock, ordered first-expiring-first across locations.

    The published interface other modules use to spend stock without caring
    which location it sits in (recipe cooking, ADR 0006). Locations holding a
    dated lot come first, soonest expiry first; undated stock trails behind, so
    perishables are consumed before staples. Zero/negative balances are omitted.
    """

    rows = db.execute(
        select(Inventory, InventoryLocation)
        .join(InventoryLocation, Inventory.location_id == InventoryLocation.id)
        .where(Inventory.item_id == item_id, Inventory.quantity > 0)
    ).all()

    stock = [
        {
            "location_id": row.location_id,
            "location_name": location.name,
            "quantity": Decimal(row.quantity),
            "unit": row.unit,
            "soonest_expiry": _soonest_lot_expiry(db, item_id, row.location_id),
        }
        for row, location in rows
    ]

    # date.max keeps undated stock last; location name breaks ties so the order
    # is stable and a cook deducts from the same place twice in a row.
    stock.sort(key=lambda entry: (entry["soonest_expiry"] or date.max, entry["location_name"]))
    return stock


def list_items_stock_fefo(
    db: Session, item_ids: list[UUID], *, location_id: UUID | None = None
) -> list[dict]:
    """FEFO stock across a *pool* of items (e.g. every product sharing a canonical
    ingredient — ADR 0015), optionally within one location.

    Each row names its concrete ``item_id`` so a caller can deduct from the actual
    stocked product even when the recipe's ingredient is a different record. Same
    first-expiring-first ordering as :func:`list_item_stock_fefo`, pooled across
    the whole set so a soon-to-expire tub in one location is spent before a staple
    in another regardless of which item record it belongs to."""

    if not item_ids:
        return []

    conditions = [Inventory.item_id.in_(item_ids), Inventory.quantity > 0]
    if location_id is not None:
        conditions.append(Inventory.location_id == location_id)

    rows = db.execute(
        select(Inventory, InventoryLocation)
        .join(InventoryLocation, Inventory.location_id == InventoryLocation.id)
        .where(*conditions)
    ).all()

    stock = [
        {
            "item_id": row.item_id,
            "location_id": row.location_id,
            "location_name": location.name,
            "quantity": Decimal(row.quantity),
            "unit": row.unit,
            "soonest_expiry": _soonest_lot_expiry(db, row.item_id, row.location_id),
        }
        for row, location in rows
    ]

    stock.sort(
        key=lambda entry: (entry["soonest_expiry"] or date.max, entry["location_name"], str(entry["item_id"]))
    )
    return stock


def list_packages_fefo(
    db: Session, item_ids: list[UUID], *, location_id: UUID | None = None
) -> list[dict]:
    """The individual PACKAGES on the shelf for a pool of items, first-expiring-first.

    A "package" is one physical thing you could pick up: one sealed container of
    a batch lot, or one opened/legacy lot taken whole. This is what a recipe line
    of "one pork butt roast" has to resolve against — the roast's weight is a
    fact about the lot, not about the catalogue entry, so no amount of arithmetic
    on the item can produce it (ADR 0025 §1/§5).

    Each entry carries the amount that package holds, in the unit its lot is
    stored in. Sealed batches expand to one entry per container, because "use one
    tub" must not mean "use all three".
    """
    if not item_ids:
        return []

    conditions = [InventoryLot.item_id.in_(item_ids), InventoryLot.quantity_remaining > 0]
    if location_id is not None:
        conditions.append(InventoryLot.location_id == location_id)

    lots = list(
        db.execute(
            select(InventoryLot)
            .where(*conditions)
            .order_by(
                InventoryLot.expiration_date.is_(None),
                InventoryLot.expiration_date.asc(),
                InventoryLot.created_at.asc(),
            )
        ).scalars()
    )

    packages: list[dict] = []
    for lot in lots:
        remaining = Decimal(lot.quantity_remaining)
        size = Decimal(lot.container_size) if lot.container_size else None
        # A sealed batch is N identical containers; an open one (or a pre-0014
        # lot with no container size) is a single package holding whatever is
        # left in it.
        count = int(lot.sealed_count or 0) if lot.status != "open" and size else 0
        if count and size:
            for _ in range(count):
                if remaining <= 0:
                    break
                packages.append(
                    {
                        "lot_id": lot.id,
                        "item_id": lot.item_id,
                        "location_id": lot.location_id,
                        "quantity": min(size, remaining),
                        "unit": lot.unit,
                        "expiration_date": lot.expiration_date,
                    }
                )
                remaining -= size
        elif remaining > 0:
            packages.append(
                {
                    "lot_id": lot.id,
                    "item_id": lot.item_id,
                    "location_id": lot.location_id,
                    "quantity": remaining,
                    "unit": lot.unit,
                    "expiration_date": lot.expiration_date,
                }
            )
    return packages


def expiry_counts(db: Session, *, within_days: int = 7) -> tuple[int, int]:
    """(expiring_soon, expired) — distinct items with live dated lots.

    Published interface for cross-module consumers (agent summary, ADR 0011);
    keeps the ``inv_lot`` projection private to this module.
    """
    from sqlalchemy import func

    today = date.today()
    live = (
        InventoryLot.quantity_remaining > 0,
        InventoryLot.expiration_date.is_not(None),
    )
    expiring_soon = db.execute(
        select(func.count(func.distinct(InventoryLot.item_id))).where(
            *live,
            InventoryLot.expiration_date >= today,
            InventoryLot.expiration_date <= today + timedelta(days=within_days),
        )
    ).scalar_one()
    expired = db.execute(
        select(func.count(func.distinct(InventoryLot.item_id))).where(
            *live, InventoryLot.expiration_date < today
        )
    ).scalar_one()
    return expiring_soon, expired


def count_active_locations(db: Session) -> int:
    return len(list_locations(db))


def list_locations(db: Session, *, include_inactive: bool = False) -> list[InventoryLocation]:
    stmt = select(InventoryLocation).order_by(InventoryLocation.name.asc())
    if not include_inactive:
        stmt = stmt.where(InventoryLocation.is_active.is_(True))
    return list(db.execute(stmt).scalars().all())


def create_location(db: Session, name: str, description: str | None) -> InventoryLocation:
    location = InventoryLocation(name=name.strip(), description=description)
    db.add(location)
    db.commit()
    db.refresh(location)
    return location


def get_location(db: Session, location_id: UUID) -> InventoryLocation | None:
    return db.execute(select(InventoryLocation).where(InventoryLocation.id == location_id)).scalar_one_or_none()


def update_location(db: Session, location: InventoryLocation, data: dict) -> InventoryLocation:
    """Partial update from a dict of set fields (typically ``model_dump(exclude_unset=True)``).

    Only keys present in ``data`` are applied, so ``description`` can be cleared
    with an explicit null while an omitted key leaves the value untouched.
    """
    if "name" in data and data["name"] is not None:
        location.name = data["name"].strip()
    if "description" in data:
        location.description = data["description"]
    if "is_active" in data and data["is_active"] is not None:
        location.is_active = data["is_active"]
    db.add(location)
    db.commit()
    db.refresh(location)
    return location


def location_in_use(db: Session, location_id: UUID) -> bool:
    """True if the location has any inventory footprint — current stock OR history.

    Guards :func:`delete_location`: several child tables cascade on delete
    (``inventory_transactions``, legacy ``inventory``, the ``inv_balance`` /
    ``inv_lot`` projections). A location that was ever stocked therefore carries
    transaction history that a hard delete would silently erase — which defeats
    the archive feature's whole preservation goal. So we block deletion whenever
    *any* such record exists (not just live stock) and steer the caller to
    archive instead; only a pristine, never-used location deletes freely.
    """
    referencing = (
        select(InventoryTransaction.id).where(InventoryTransaction.location_id == location_id),
        select(Inventory.id).where(Inventory.location_id == location_id),
        select(InventoryBalance.id).where(InventoryBalance.location_id == location_id),
        select(InventoryLot.id).where(InventoryLot.location_id == location_id),
    )
    return any(db.execute(stmt.limit(1)).first() is not None for stmt in referencing)


def delete_location(db: Session, location: InventoryLocation) -> None:
    db.delete(location)
    db.commit()


def get_item(db: Session, item_id: UUID) -> Item | None:
    return db.execute(select(Item).where(Item.id == item_id)).scalar_one_or_none()


def _get_inventory_row(db: Session, *, item_id: UUID, location_id: UUID) -> Inventory | None:
    """The current stock row for an item at a location, or None — never creates."""
    return db.execute(
        select(Inventory).where(Inventory.item_id == item_id, Inventory.location_id == location_id)
    ).scalar_one_or_none()


# Quantities land in Numeric(12,3) columns. A container size travels in the
# event PAYLOAD instead — free-form JSON, no column to round it — so without
# this the lot claims a container fractionally larger than the quantity backing
# it. Consuming the reported amount then overdraws by a ten-thousandth of a gram
# and cracks the next sealed container: "use one roast" quietly opens two.
_STORED = Decimal("0.001")


def _as_stored(value: Decimal) -> Decimal:
    """Round to the precision quantities are actually persisted at."""
    return Decimal(value).quantize(_STORED, rounding=ROUND_HALF_UP)


def base_unit_of(item: Item) -> str:
    """The item's base (stored & consumed) unit — each / g / ml (ADR 0016)."""
    return base_unit_for(item.base_unit, item.stock_mode, item.size_unit)


def convert_amount(
    db: Session,
    item: Item,
    quantity: Decimal,
    *,
    from_unit: str,
    to_unit: str,
    hints: tuple[Decimal | None, Decimal | None] | None = None,
) -> Decimal | None:
    """Convert ``quantity`` from one unit to another for a given item, or None if
    not derivable.

    Bridges count↔volume/mass using the item's package size (one 'each' = its net
    contents) plus density / weight-per-each — resolved from the item's own columns
    OR the seeded ``ingredient_reference`` by name/category (ADR 0016 Phase 2), so
    "1 tsp" of milk/oil/flour converts without a per-item override. Never guesses —
    a missing bridge yields None so the caller refuses rather than mis-decrements.

    Pass ``hints`` (density, grams_per_each) to reuse a preloaded reference index
    and skip the per-call lookup in hot loops (ADR 0017 §10).
    """
    if normalize_unit(from_unit) == normalize_unit(to_unit):
        return Decimal(quantity)

    density, explicit_gpe = hints if hints is not None else resolve_hints(db, item)
    grams_each = grams_per_each_from_size(
        item.size_quantity, item.size_unit, density_g_per_ml=density, grams_per_each=explicit_gpe
    )
    ml_each = ml_per_each_from_size(
        item.size_quantity, item.size_unit, density_g_per_ml=density, grams_per_each=explicit_gpe
    )
    return convert_quantity(
        Decimal(quantity),
        from_unit,
        to_unit,
        density_g_per_ml=density,
        grams_per_each=grams_each,
        ml_per_each=ml_each,
    )


def convert_to_base(
    db: Session,
    item: Item,
    quantity: Decimal,
    unit: str,
    *,
    hints: tuple[Decimal | None, Decimal | None] | None = None,
) -> Decimal | None:
    """Recipe/consumption semantics (ADR 0016/0017): ``quantity {unit}`` expressed
    in the item's base unit. A count unit is that many base units (NOT a package —
    that's intake). None when there is no bridge to base."""
    return convert_amount(db, item, Decimal(quantity), from_unit=unit, to_unit=base_unit_of(item), hints=hints)


def _convert_to_stock_unit(
    db: Session, item: Item, quantity: Decimal, *, from_unit: str, to_unit: str
) -> Decimal | None:
    """Back-compat alias for :func:`convert_amount` (per-call hint resolution)."""
    return convert_amount(db, item, quantity, from_unit=from_unit, to_unit=to_unit)


def _get_or_create_inventory_row(
    db: Session,
    *,
    item_id: UUID,
    location_id: UUID,
    unit: str,
    expiration_date: date | None,
) -> Inventory:
    row = db.execute(
        select(Inventory).where(Inventory.item_id == item_id, Inventory.location_id == location_id)
    ).scalar_one_or_none()
    if row:
        return row

    row = Inventory(
        item_id=item_id,
        location_id=location_id,
        quantity=Decimal("0"),
        unit=unit,
        expiration_date=expiration_date,
    )
    db.add(row)
    db.flush()
    return row


def _build_inventory_line(row: Inventory, item_name: str) -> dict:
    return {
        "inventory_id": row.id,
        "item_id": row.item_id,
        "item_name": item_name,
        "location_id": row.location_id,
        "quantity": row.quantity,
        "unit": row.unit,
        "expiration_date": row.expiration_date,
        "updated_at": row.updated_at,
    }


def get_inventory_by_location(db: Session, location_id: UUID) -> list[dict]:
    """Read the current inventory for a location from the event-derived
    ``inv_balance`` projection (ADR 0002 read cutover). Expiry on each line is
    the soonest-to-expire live lot, or None when no lot carries a date."""
    rows = db.execute(
        select(InventoryBalance, Item)
        .join(Item, InventoryBalance.item_id == Item.id)
        .where(InventoryBalance.location_id == location_id)
        .order_by(Item.name.asc())
    ).all()

    return [
        {
            "inventory_id": balance.id,
            "item_id": balance.item_id,
            "item_name": item.name,
            "location_id": balance.location_id,
            "quantity": balance.quantity,
            "unit": balance.unit,
            "expiration_date": _soonest_lot_expiry(db, balance.item_id, balance.location_id),
            "updated_at": balance.updated_at,
        }
        for balance, item in rows
    ]


def scan_location_candidates(db: Session, item_id: UUID, direction: str) -> list[dict]:
    """Locations relevant to a location-less scan (scan-station feedback).

    For ``out`` (consume): every location the item is actually in stock (qty > 0) —
    one means we can consume without asking, several means prompt, none means it's
    nowhere to take from. For ``in`` (add): where it already lives, so restocking
    lands where the item is; if it lives nowhere yet, all locations are offered as
    add targets. Each option carries current on-hand for a clear prompt."""
    in_stock = [
        {"location_id": s["location_id"], "location_name": s["location_name"], "quantity": str(s["quantity"])}
        for s in list_item_stock_fefo(db, item_id)
    ]
    if direction == "out" or in_stock:
        return in_stock

    locations = db.execute(select(InventoryLocation).order_by(InventoryLocation.name.asc())).scalars().all()
    return [{"location_id": loc.id, "location_name": loc.name, "quantity": "0"} for loc in locations]


def _resolve_scan_item(db: Session, *, barcode: str | None, item_id: UUID | None) -> Item:
    """Identify the item a scan-station move applies to: a direct item_id (the
    name-search dropdown already knows which item) takes precedence over a
    barcode lookup. Raises LookupError when neither resolves."""
    if item_id is not None:
        item = get_item(db, item_id)
        if item is None:
            raise LookupError("Item not found")
        return item

    from modules.catalog.services.barcodes import resolve_local_item

    item = resolve_local_item(db, barcode or "")
    if item is None:
        raise LookupError("Unknown barcode — add the item in Catalog first")
    return item


def resolve_scan(
    db: Session, *, barcode: str | None = None, item_id: UUID | None = None, direction: str, location_id: UUID | None
) -> dict:
    """Work out where a scan should land before moving anything (scan-station
    feedback). Returns ``{"item", "location_id", "candidates"}``: a concrete
    ``location_id`` when one is given or unambiguously resolved, else
    ``location_id=None`` with ``candidates`` to choose from. Raises LookupError for
    an unknown barcode/item and ValueError when there's nothing to consume
    anywhere. Identify the item by ``barcode`` (scanned/typed) or ``item_id``
    (picked from the name-search dropdown) — exactly one is expected."""
    item = _resolve_scan_item(db, barcode=barcode, item_id=item_id)

    if location_id is not None:
        return {"item": item, "location_id": location_id, "candidates": []}

    candidates = scan_location_candidates(db, item.id, direction)
    if len(candidates) == 1:
        return {"item": item, "location_id": candidates[0]["location_id"], "candidates": []}
    if len(candidates) == 0:  # only 'out' with the item nowhere in stock
        raise ValueError(f"{item.name} isn't in stock anywhere to consume")
    return {"item": item, "location_id": None, "candidates": candidates}


def plan_container_scan_out(db: Session, *, item_id: UUID, location_id: UUID) -> dict:
    """Decide what "scan this empty container out" means for a container-tracked
    item at one location (ADR 0023 D2).

    Scanning an empty tub reads its *product* barcode, which names the item, not
    the container — so the gesture can never disambiguate itself. Returns one of:

    - ``{"status": "legacy"}`` — no container lots here; the caller uses the
      ordinary quantity-based consume, exactly as before.
    - ``{"status": "targeted", "lot_id": ...}`` — one open container (finish it),
      or none open (a whole sealed one was eaten in a sitting).
    - ``{"status": "needs_container", "candidates": [...]}`` — two or more are
      open. Which one was emptied is a fact only the person holding it has, so it
      is asked, never guessed (D3). Candidates come back soonest-expiry first.

    Note the deliberate asymmetry: picking among *sealed* containers is done
    FEFO with no prompt, because sealed containers are indistinguishable to the
    person holding one. The prompt exists for what the human knows and the
    system cannot see, not for every ambiguity on paper.
    """
    lots = [
        lot
        for lot in db.execute(
            select(InventoryLot).where(
                InventoryLot.item_id == item_id,
                InventoryLot.location_id == location_id,
                InventoryLot.quantity_remaining > 0,
            )
        )
        .scalars()
        .all()
    ]
    if not any(lot.container_size is not None for lot in lots):
        return {"status": "legacy"}

    def _fefo(rows: list[InventoryLot]) -> list[InventoryLot]:
        return sorted(rows, key=lambda lot: (lot.expiration_date is None, lot.expiration_date, lot.created_at))

    opens = _fefo([lot for lot in lots if lot.status == "open"])
    if len(opens) == 1:
        return {"status": "targeted", "lot_id": opens[0].id}
    if len(opens) > 1:
        return {"status": "needs_container", "candidates": opens}

    sealed = _fefo([lot for lot in lots if lot.container_size is not None and (lot.sealed_count or 0) > 0])
    if sealed:
        return {"status": "targeted", "lot_id": sealed[0].id}
    return {"status": "legacy"}


def balance_line(db: Session, *, item_id: UUID, location_id: UUID, item_name: str) -> dict:
    """One on-hand line straight off the ``inv_balance`` projection, shaped like
    ``InventoryLineRead``. Used by the lot-targeted paths, which never touch the
    legacy row's quantity as their source of truth."""
    balance = db.execute(
        select(InventoryBalance).where(
            InventoryBalance.item_id == item_id, InventoryBalance.location_id == location_id
        )
    ).scalar_one_or_none()
    if balance is None:
        # Fully drained: the projection drops zero balances, so there is no row
        # to read. The line still has to render ("now 0"), not vanish.
        item = get_item(db, item_id)
        return {
            "inventory_id": uuid_module.uuid4(),
            "item_id": item_id,
            "item_name": item_name,
            "location_id": location_id,
            "quantity": Decimal("0"),
            "unit": base_unit_of(item) if item else "each",
            "expiration_date": None,
            "updated_at": datetime.now(UTC),
        }
    return {
        "inventory_id": balance.id,
        "item_id": item_id,
        "item_name": item_name,
        "location_id": location_id,
        "quantity": balance.quantity,
        "unit": balance.unit,
        "expiration_date": _soonest_lot_expiry(db, item_id, location_id),
        "updated_at": balance.updated_at,
    }


def scan_move(
    db: Session,
    *,
    barcode: str | None = None,
    item_id: UUID | None = None,
    location_id: UUID,
    direction: str,
    quantity: Decimal,
    unit: str,
    expiration_date: date | None = None,
    actor: str | None = None,
) -> tuple[InventoryTransaction, Inventory, Item, str]:
    """Scan-station move (step 3): resolve the item (by ``barcode`` or a direct
    ``item_id`` from the name-search dropdown) and add (``in``) or consume
    (``out``) it at ``location_id``, appending the matching ledger event
    attributed to ``actor``. An add may carry a best-by (``expiration_date``);
    it's ignored on consume. Raises LookupError for an unknown barcode/item and
    ValueError for a bad direction / insufficient stock."""
    item = _resolve_scan_item(db, barcode=barcode, item_id=item_id)

    if direction == "in":
        # Check in as CONTAINERS whenever the item's package size is known
        # (ADR 0023 D9). A quick scan-in used to record a bare quantity, and
        # container-less stock can never be finished by container — so the fast
        # lane silently produced stock the consume side couldn't handle. The
        # size is already on the catalog item, so nobody has to type anything.
        #
        # Only for whole counts of a count unit: "3 each" is three packages,
        # whereas "1.5 lb" is a measured amount with no container to speak of.
        containers: dict = {}
        qty = Decimal(quantity)
        if (
            item.size_quantity
            and item.size_unit
            and is_count_unit(unit)
            and qty == qty.to_integral_value()
            and qty > 0
        ):
            containers = {
                "container_size": Decimal(str(item.size_quantity)),
                "container_unit": item.size_unit,
                "sealed_count": int(qty),
            }
        tx, row, _item = add_inventory(
            db,
            item_id=item.id,
            location_id=location_id,
            quantity=quantity,
            unit=unit,
            expiration_date=expiration_date,
            reason="scan_in",
            note=None,
            actor=actor,
            **containers,
        )
    elif direction == "out":
        tx, row, _item = remove_inventory(
            db,
            item_id=item.id,
            location_id=location_id,
            quantity=quantity,
            unit=unit,
            reason="scan_out",
            note=None,
            actor=actor,
            # The scan station is someone holding the package. If less than a
            # full one is left, finishing it is what they mean; erroring leaves
            # a part-used remainder on the books forever with no way to clear it.
            take_remainder=True,
        )
    else:
        raise ValueError("direction must be 'in' or 'out'")

    return tx, row, item, direction


def get_events(
    db: Session,
    location_id: UUID | None = None,
    limit: int = 50,
    since_hours: int | None = None,
    event_types: tuple[str, ...] | None = None,
) -> list[dict]:
    """Authoritative inventory history from the append-only event log (ADR 0002
    §5). Unlike the legacy ``/transactions`` view, this is the source of truth.

    ``since_hours`` bounds the window and ``event_types`` narrows the kinds —
    together they let a caller ask "what came off the shelves today" without
    pulling the whole log. Both are applied in the query, not after, so the
    ``limit`` counts rows the caller actually wanted.
    """
    # Outer-joined: location_id is nullable on the event (off-pantry meals carry
    # none), and an inner join would silently drop those rows from history.
    stmt = (
        select(InventoryEvent, Item, InventoryLocation.name)
        .join(Item, InventoryEvent.item_id == Item.id)
        .outerjoin(InventoryLocation, InventoryEvent.location_id == InventoryLocation.id)
        .order_by(InventoryEvent.seq.desc())
        .limit(limit)
    )
    if since_hours is not None:
        cutoff = datetime.now(timezone.utc) - timedelta(hours=since_hours)
        stmt = stmt.where(InventoryEvent.occurred_at >= cutoff)
    if event_types:
        stmt = stmt.where(InventoryEvent.event_type.in_(event_types))
    if location_id is not None:
        stmt = stmt.where(
            or_(
                InventoryEvent.location_id == location_id,
                InventoryEvent.target_location_id == location_id,
            )
        )

    rows = db.execute(stmt).all()
    return [
        {
            "event_id": event.event_id,
            "seq": event.seq,
            "event_type": event.event_type,
            "item_id": event.item_id,
            "item_name": item.name,
            "location_id": event.location_id,
            "location_name": location_name,
            "target_location_id": event.target_location_id,
            "quantity": event.quantity,
            "unit": event.unit,
            "expiration_date": event.expiration_date,
            "reason": event.reason,
            "note": event.note,
            "actor": event.actor,
            "source": event.source,
            "occurred_at": event.occurred_at,
        }
        for event, item, location_name in rows
    ]


def _to_base_on_intake(db: Session, item: Item, quantity: Decimal, unit: str) -> tuple[Decimal, str]:
    """Convert an intake amount into the item's base unit (ADR 0016).

    A count/pack unit is a *package* → its net contents in base (1 pack → 20 each;
    1 jug → 1893 ml; a package with no size stays 1 base-each). A mass/volume amount
    converts straight to base. Raises when there is no bridge, so we refuse rather
    than stock a wrong number."""
    base = base_unit_for(item.base_unit, item.stock_mode, item.size_unit)
    density = Decimal(item.density_g_per_ml) if item.density_g_per_ml is not None else None
    gpe = Decimal(item.grams_per_each) if item.grams_per_each is not None else None
    if is_count_unit(unit):
        net = net_contents_in_base(
            item.base_unit,
            item.stock_mode,
            item.size_quantity,
            item.size_unit,
            density_g_per_ml=density,
            grams_per_each=gpe,
        )
        return (Decimal(quantity) * net if net is not None else Decimal(quantity)), base
    converted = _convert_to_stock_unit(db, item, Decimal(quantity), from_unit=unit, to_unit=base)
    if converted is None:
        # No bridge to the base unit (an under-specified item — no size/density —
        # added in a mass/volume unit, e.g. size-less flour in cups). Don't refuse
        # the intake: store it in the unit given (pre-0016 leniency). The balance is
        # then self-consistent in that unit, and becomes fully base-expressed once a
        # size is set. Consumption still refuses an unconvertible unit.
        return Decimal(quantity), unit
    return converted, base


def add_inventory(
    db: Session,
    *,
    item_id: UUID,
    location_id: UUID,
    quantity: Decimal,
    unit: str,
    expiration_date: date | None,
    reason: str | None,
    note: str | None,
    actor: str | None = None,
    container_id: UUID | None = None,
    container_size: Decimal | None = None,
    container_unit: str | None = None,
    sealed_count: int | None = None,
    unit_price: Decimal | None = None,
    deferred_events: list | None = None,
) -> tuple[InventoryTransaction, Inventory, Item]:
    from modules.inventory.services.containers import resolve_container

    item = get_item(db, item_id)
    if not item:
        raise LookupError("Item not found")

    location = get_location(db, location_id)
    if not location:
        raise LookupError("Location not found")

    # Store in the item's base unit (ADR 0016), and — when a container batch is
    # given (ADR 0014) — ALSO record the per-container size *in base* so sealed/open
    # tracking and FEFO run in the same unit as the balance. The two models compose:
    #   • plain package (no container form): explode to net contents
    #     (20-pouch pack → 20 each; ½-gal jug → 1893 ml; 2-oz jar → 56.7 g);
    #   • container batch: convert ONE container to base, scale by the sealed count,
    #     and keep the per-container base size on the lot payload.
    # A direct measured amount (500 g, 1 cup) still converts straight to base.
    lot_payload: dict | None = None

    # Catch weight (ADR 0025 §1): this product's packages all scan the same UPC
    # and all weigh something different, so the item's size is a NOMINAL and the
    # weight of the package in hand is the only true number. Every check-in is
    # therefore one package of a known weight, whichever form it arrives in.
    if item.variable_weight and container_size is None:
        if is_count_unit(unit):
            nominal = (
                f" (nominally {_trim(Decimal(item.size_quantity))} {item.size_unit})"
                if item.size_quantity and item.size_unit
                else ""
            )
            raise ValueError(
                f"{item.name} weighs something different in every package{nominal} — "
                f"check it in by weight (e.g. 4.87 lb) instead of by the each, or send "
                f"container_size with the weight of this one."
            )
        # A weight WAS given: that is this package. Routing it through the
        # container form is what makes "how many roasts do I have" answerable
        # alongside "how many grams", instead of one anonymous pile of meat.
        container_size, container_unit = Decimal(quantity), unit
        sealed_count = sealed_count or 1
    elif item.variable_weight and sealed_count is None:
        sealed_count = 1

    if container_size is not None and sealed_count is not None:
        # Refresh the item's package size from what was checked in and RE-DERIVE its
        # base unit FIRST, so an under-specified item lands the right base (a measured
        # item's first ½-gal check-in becomes base ml, not each) before we convert
        # into it. Mirrors the catalog's update_item recompute (ADR 0016).
        #
        # Never for a variable-weight item: there, the size is a nominal and this
        # package's weight is a fact about the package, not the product. Writing it
        # back would make the catalog's size mean "whatever was scanned last" and
        # silently re-rule every lot booked before it.
        if container_unit and not item.variable_weight:
            item.size_quantity = Decimal(container_size)
            item.size_unit = container_unit
            item.base_unit = default_base_unit(item.stock_mode, item.size_unit)
            db.add(item)
        base = base_unit_for(item.base_unit, item.stock_mode, item.size_unit)
        size_base = _convert_to_stock_unit(
            db, item, Decimal(container_size), from_unit=(container_unit or unit), to_unit=base
        )
        if size_base is None:
            # No bridge to base (under-specified item) — keep the given size/unit so
            # the batch is at least self-consistent; consumption still refuses later.
            size_base, base = Decimal(container_size), (container_unit or unit)
        size_base = _as_stored(size_base)
        quantity = size_base * Decimal(sealed_count)
        unit = base
        lot_payload = {
            "container_size": str(size_base),
            "container_unit": base,
            "sealed_count": int(sealed_count),
        }
    else:
        quantity, unit = _to_base_on_intake(db, item, Decimal(quantity), unit)

    container_id = resolve_container(db, container_id, location_id)

    row = _get_or_create_inventory_row(
        db,
        item_id=item_id,
        location_id=location_id,
        unit=unit,
        expiration_date=expiration_date,
    )
    row.quantity = Decimal(row.quantity) + Decimal(quantity)
    row.unit = unit
    if expiration_date is not None:
        row.expiration_date = expiration_date

    tx = InventoryTransaction(
        item_id=item_id,
        location_id=location_id,
        quantity_change=Decimal(quantity),
        unit=unit,
        transaction_type="add",
        reason=reason,
        note=note,
    )
    db.add(tx)
    db.add(row)

    # Ledger (ADR 0002): intake is a `purchased` fact; a lot is created carrying
    # the (optional) expiry/price. rebuild_projections folds it synchronously.
    repository.append_event(
        db,
        event_type="purchased",
        item_id=item_id,
        location_id=location_id,
        quantity=Decimal(quantity),
        unit=unit,
        expiration_date=expiration_date,
        container_id=container_id,
        unit_price=unit_price,
        source="api",
        actor=actor,
        reason=reason,
        note=note,
        payload=lot_payload,
    )
    rebuild_projections(db)

    db.commit()
    db.refresh(tx)
    db.refresh(row)

    # Transaction-owning consumers collect notifications until THEIR commit.
    event = InventoryAdded(
        item_id=item_id, location_id=location_id, quantity_added=Decimal(quantity),
        quantity_after=Decimal(row.quantity), unit=unit, reason=reason, note=note,
    )
    if deferred_events is None:
        get_event_bus().publish(event, db)
    else:
        deferred_events.append(event)
    return tx, row, item


def produce_inventory(
    db: Session,
    *,
    item_id: UUID,
    location_id: UUID,
    quantity: Decimal,
    unit: str,
    expiration_date: date | None = None,
    reason: str | None = None,
    note: str | None = None,
    actor: str | None = None,
    weight_grams: Decimal | None = None,
    correlation_id: UUID | None = None,
    container_id: UUID | None = None,
    container_size: Decimal | None = None,
    container_unit: str | None = None,
    sealed_count: int | None = None,
) -> tuple[InventoryEvent, Inventory, Item]:
    """Transformation output (ADR 0003 D3): record a `produced` fact that creates
    a new tracked lot at ``location_id`` (the "bread out of flour" side of a cook).

    Mirrors ``add_inventory`` — dual-writes the legacy row/transaction and folds
    the ledger in the same transaction — but the event is `produced` (not
    `purchased`) so the yield reads as a transformation, not a purchase. An
    optional finished-dish ``weight_grams`` is stored on the event payload.

    ``container_size``/``sealed_count`` pack the yield into discrete containers —
    three tubs of pulled pork rather than one undifferentiated blob (ADR 0025 §4).
    The lot projector already folds these for a `produced` event exactly as it
    does for an intake (``domain/lots.py``), so each tub can then be finished,
    discarded, or expired on its own. Unlike ``add_inventory``'s container form,
    the count does NOT redefine the quantity: a cook's yield is what it is, and
    the containers describe how it was stored."""
    from modules.inventory.services.containers import resolve_container

    item = get_item(db, item_id)
    if not item:
        raise LookupError("Item not found")

    location = get_location(db, location_id)
    if not location:
        raise LookupError("Location not found")

    # Store the yield in the output item's base unit (ADR 0017 §8): producing "500 g
    # bread" or "1 loaf" must land in base, exactly like intake. Uses consumption
    # semantics (a count unit is that many base units, not a package); an
    # unconvertible output unit falls back to the given unit (pre-0016 leniency).
    given_unit = unit  # `unit` becomes the base below; a container size given
    # without its own unit is in the unit the caller expressed the yield in.
    converted = convert_to_base(db, item, Decimal(quantity), unit)
    if converted is not None:
        quantity, unit = converted, base_unit_of(item)

    # One container's size goes to base alongside the quantity, so the lot's
    # sealed/open arithmetic runs in the same unit as the balance it draws from.
    size_base: Decimal | None = None
    if container_size is not None and sealed_count:
        size_base = convert_to_base(db, item, Decimal(container_size), container_unit or given_unit)
        if size_base is None:
            size_base = Decimal(container_size)  # no bridge — keep the batch self-consistent

    container_id = resolve_container(db, container_id, location_id)

    row = _get_or_create_inventory_row(
        db,
        item_id=item_id,
        location_id=location_id,
        unit=unit,
        expiration_date=expiration_date,
    )
    row.quantity = Decimal(row.quantity) + Decimal(quantity)
    row.unit = unit
    if expiration_date is not None:
        row.expiration_date = expiration_date

    tx = InventoryTransaction(
        item_id=item_id,
        location_id=location_id,
        quantity_change=Decimal(quantity),
        unit=unit,
        transaction_type="produce",
        reason=reason,
        note=note,
    )
    db.add(tx)
    db.add(row)

    payload: dict = {}
    if weight_grams is not None:
        payload["weight_grams"] = str(weight_grams)
    if size_base is not None:
        payload.update(
            {
                "container_size": str(size_base),
                "container_unit": unit,
                "sealed_count": int(sealed_count),
            }
        )

    # Ledger: `produced` creates a lot on replay (like `purchased`), carrying the
    # optional expiry and finished-dish weight. rebuild_projections folds it now.
    event = repository.append_event(
        db,
        event_type="produced",
        item_id=item_id,
        location_id=location_id,
        quantity=Decimal(quantity),
        unit=unit,
        expiration_date=expiration_date,
        container_id=container_id,
        source="api",
        actor=actor,
        reason=reason,
        note=note,
        correlation_id=correlation_id,
        payload=payload or None,
    )
    rebuild_projections(db)

    db.commit()
    db.refresh(event)
    db.refresh(row)

    get_event_bus().publish(
        InventoryAdded(
            item_id=item_id,
            location_id=location_id,
            quantity_added=Decimal(quantity),
            quantity_after=Decimal(row.quantity),
            unit=unit,
            reason=reason or "produced",
            note=note,
        ),
        db,
    )
    return event, row, item


def list_lots(
    db: Session,
    *,
    item_id: UUID | None = None,
    location_id: UUID | None = None,
    include_depleted: bool = False,
) -> list[InventoryLot]:
    """Lot/container breakdown for display + discard targeting (ADR 0014).

    Ordered FEFO: dated soonest-first, undated last. Sealed batches and open
    containers are both returned (distinguished by ``status``)."""
    stmt = select(InventoryLot)
    if item_id is not None:
        stmt = stmt.where(InventoryLot.item_id == item_id)
    if location_id is not None:
        stmt = stmt.where(InventoryLot.location_id == location_id)
    if not include_depleted:
        stmt = stmt.where(InventoryLot.quantity_remaining > 0)
    stmt = stmt.order_by(
        InventoryLot.expiration_date.is_(None), InventoryLot.expiration_date.asc(), InventoryLot.created_at.asc()
    )
    return list(db.execute(stmt).scalars().all())


def open_container(db: Session, *, lot_id: UUID, actor: str | None = None) -> InventoryLot:
    """Open one sealed container of a batch lot — materialises an open container
    with its full content (ADR 0014). Idempotent-ish: opening a lot with no
    sealed containers left is a no-op fold."""
    lot = db.get(InventoryLot, lot_id)
    if lot is None:
        raise LookupError("Lot not found")
    repository.append_event(
        db,
        event_type="opened",
        item_id=lot.item_id,
        location_id=lot.location_id,
        quantity=Decimal("0"),
        unit=lot.unit,
        lot_id=lot.id,
        source="api",
        actor=actor,
    )
    rebuild_projections(db)
    db.commit()
    return lot


def lot_identity(db: Session, lot_id: UUID) -> tuple[UUID, UUID] | None:
    """The (item, location) a lot belongs to, or None if the ledger has never
    heard of it.

    A fully-drained lot is pruned from the ``inv_lot`` projection, so "missing
    from the projection" means either *already empty* or *never existed* — and
    those must not be answered the same way. The event log still carries the
    drained lot's id, so it settles which one this is.
    """
    lot = db.get(InventoryLot, lot_id)
    if lot is not None:
        return lot.item_id, lot.location_id
    event = db.execute(
        select(InventoryEvent).where(InventoryEvent.lot_id == lot_id).order_by(InventoryEvent.seq.asc()).limit(1)
    ).scalar_one_or_none()
    if event is None or event.location_id is None:
        return None
    return event.item_id, event.location_id


def _targeted_container_quantity(lot: InventoryLot, quantity: Decimal | None) -> Decimal:
    """How much a lot-targeted action takes: an explicit ``quantity``, else the
    whole of an open container (or legacy lot), else exactly ONE sealed unit of a
    batch. Never more than the lot actually holds.

    The sealed-batch rule is what stops "I finished the yogurt" from consuming
    all three tubs at once.
    """
    if quantity is not None:
        qty = Decimal(quantity)
    elif lot.status == "open" or lot.container_size is None:
        qty = Decimal(lot.quantity_remaining)  # open container / legacy lot → all of it
    else:
        qty = Decimal(lot.container_size)  # one sealed container of the batch
    return min(qty, Decimal(lot.quantity_remaining))


def _sync_legacy_balance(db: Session, lot: InventoryLot, qty: Decimal) -> None:
    """Decrement the legacy ``inventory`` row alongside a lot-targeted removal.

    Reads come from the ``inv_balance`` projection (ADR 0002 read cutover), but
    the legacy row still backs ``remove_inventory``'s stock-unit resolution and
    its insufficient-stock guard. Leaving it untouched lets it drift high, which
    quietly makes that guard too permissive. Skipped on a unit mismatch — a wrong
    decrement is worse than a stale one.
    """
    row = _get_inventory_row(db, item_id=lot.item_id, location_id=lot.location_id)
    if row is None or normalize_unit(row.unit) != normalize_unit(lot.unit):
        return
    row.quantity = max(Decimal("0"), Decimal(row.quantity) - qty)
    db.add(row)


def _targeted_lot_removal(
    db: Session,
    *,
    lot: InventoryLot,
    qty: Decimal,
    event_type: str,
    transaction_type: str,
    reason: str | None,
    note: str | None,
    actor: str | None,
) -> InventoryTransaction:
    """Shared spine for every lot-targeted removal: append the ledger event
    naming the lot, mirror it as a transaction (so the existing reversal path
    can undo it), sync the legacy balance, and refold the projections."""
    tx = InventoryTransaction(
        item_id=lot.item_id,
        location_id=lot.location_id,
        quantity_change=Decimal("0") - qty,
        unit=lot.unit,
        transaction_type=transaction_type,
        reason=reason,
        note=note,
        lot_id=lot.id,
    )
    db.add(tx)
    _sync_legacy_balance(db, lot, qty)
    item_id, location_id = lot.item_id, lot.location_id
    repository.append_event(
        db,
        event_type=event_type,
        item_id=item_id,
        location_id=location_id,
        quantity=qty,
        unit=lot.unit,
        lot_id=lot.id,
        source="api",
        actor=actor,
        reason=reason,
        note=note,
        # How many whole sealed containers this took (0 when it drew from an
        # already-open one). Recorded so an undo can put the container count
        # back, not just the quantity — see `_create_lot`'s restore branch.
        payload=_container_delta(lot, qty),
    )
    rebuild_projections(db)
    db.commit()
    db.refresh(tx)

    # Same domain event the aggregate removal path publishes, so an integration
    # (the Home Assistant webhook, ADR 0011) sees container removals too —
    # otherwise external state silently drifts for exactly the items this ADR
    # made easiest to consume.
    _publish_removal(db, item_id=item_id, location_id=location_id, quantity=qty, unit=tx.unit, reason=reason, note=note)
    return tx


def _container_delta(lot: InventoryLot, qty: Decimal) -> dict:
    """The container-count effect of removing ``qty`` from ``lot``, mirroring
    ``_reduce_targeted_lot``'s arithmetic in the fold."""
    dropped = 0
    if lot.status != "open" and lot.container_size:
        dropped = int((Decimal(qty) / Decimal(lot.container_size)).to_integral_value(rounding=ROUND_HALF_UP))
    return {
        "sealed_count": dropped,
        "container_size": None if lot.container_size is None else str(lot.container_size),
        "container_unit": lot.container_unit,
    }


def _original_container_delta(db: Session, *, lot_id: UUID, quantity: Decimal) -> dict | None:
    """The container payload recorded by the removal a reversal is undoing.

    Matched on lot and amount, newest first — a lot can be finished, restored and
    finished again, and the most recent matching removal is the one being undone.
    Returns None for a pre-0023 removal that recorded no container metadata, in
    which case the fold restores quantity only, exactly as it did before."""
    event = db.execute(
        select(InventoryEvent)
        .where(
            InventoryEvent.lot_id == lot_id,
            InventoryEvent.event_type.in_(("consumed", "spoiled")),
            InventoryEvent.quantity == quantity,
        )
        .order_by(InventoryEvent.seq.desc())
        .limit(1)
    ).scalar_one_or_none()
    return event.payload if event is not None else None


def _publish_removal(
    db: Session,
    *,
    item_id: UUID,
    location_id: UUID,
    quantity: Decimal,
    unit: str,
    reason: str | None,
    note: str | None,
) -> None:
    """Announce a removal on the event bus with the balance as it now stands."""
    balance = db.execute(
        select(InventoryBalance).where(
            InventoryBalance.item_id == item_id, InventoryBalance.location_id == location_id
        )
    ).scalar_one_or_none()
    get_event_bus().publish(
        InventoryRemoved(
            item_id=item_id,
            location_id=location_id,
            quantity_removed=Decimal(quantity),
            # The projection drops zero balances, so a fully drained item has no
            # row left — that is a balance of 0, not a missing fact.
            quantity_after=Decimal(balance.quantity) if balance is not None else Decimal("0"),
            unit=unit,
            reason=reason,
            note=note,
        ),
        db,
    )


def discard_container(
    db: Session,
    *,
    lot_id: UUID,
    quantity: Decimal | None = None,
    actor: str | None = None,
    reason: str | None = "spoiled",
    note: str | None = None,
) -> tuple[InventoryTransaction | None, Decimal]:
    """Discard/spoil a specific container (ADR 0014): a whole open container, or
    one sealed unit of a batch, or an explicit ``quantity``. This is the 'throw
    out the spoiled jug without touching the others' action — it targets THIS
    lot, not FEFO.

    Returns ``(transaction, amount)``. An already-empty lot is a no-op returning
    ``(None, 0)`` rather than an error (ADR 0023 D13): two people can reach for
    the same spoiled container, and the second tap must not fall through to
    something else.
    """
    lot = db.get(InventoryLot, lot_id)
    if lot is None:
        # Drained and pruned from the projection = already resolved, not an
        # error; genuinely unknown = a real 404 (ADR 0023 D13).
        if lot_identity(db, lot_id) is not None:
            return None, Decimal("0")
        raise LookupError("Lot not found")

    qty = _targeted_container_quantity(lot, quantity)
    if qty <= 0:
        return None, Decimal("0")

    tx = _targeted_lot_removal(
        db,
        lot=lot,
        qty=qty,
        event_type="spoiled",
        transaction_type="remove",
        reason=reason,
        note=note,
        actor=actor,
    )
    return tx, qty


def consume_container(
    db: Session,
    *,
    lot_id: UUID,
    quantity: Decimal | None = None,
    actor: str | None = None,
    reason: str | None = "finished",
    note: str | None = None,
) -> tuple[InventoryTransaction | None, Decimal]:
    """Consume from a specific container (ADR 0023 D1) — the counterpart to
    ``discard_container`` for stock that was *used*, not wasted.

    ``quantity=None`` means "I finished this one": the whole of an open
    container, or one sealed unit of a batch. An explicit ``quantity`` is a
    partial draw from that same container.

    Why targeted rather than FEFO: with two tubs of yogurt open, which one was
    emptied is a fact only the person holding it has. Guessing corrupts the
    surviving container's expiry and invents a phantom open one, so the event
    names the lot and the projector honours it.

    Unlike ``remove_inventory`` this needs no unit conversion — it consumes the
    lot's own quantity in the lot's own unit — so it works on items that have no
    bridge to a base unit at all.

    Returns ``(transaction, amount)``; an already-empty container is a no-op
    returning ``(None, 0)``.
    """
    lot = db.get(InventoryLot, lot_id)
    if lot is None:
        # Drained and pruned from the projection = already resolved, not an
        # error; genuinely unknown = a real 404 (ADR 0023 D13).
        if lot_identity(db, lot_id) is not None:
            return None, Decimal("0")
        raise LookupError("Lot not found")

    qty = _targeted_container_quantity(lot, quantity)
    if qty <= 0:
        return None, Decimal("0")

    tx = _targeted_lot_removal(
        db,
        lot=lot,
        qty=qty,
        event_type="consumed",
        transaction_type="remove",
        reason=reason,
        note=note,
        actor=actor,
    )
    return tx, qty


def set_item_expiration(
    db: Session, *, item_id: UUID, location_id: UUID, expiration_date: date, actor: str | None = None
) -> int:
    """Set the best-by date for an item's live stock at a location by relabeling
    its lot(s) — the correct event-sourced way to change a displayed expiry
    (the on-hand line shows the soonest live lot's date). Returns lots relabeled."""
    lots = list_lots(db, item_id=item_id, location_id=location_id)
    if not lots:
        raise LookupError("No stock to set a date on at this location")
    for lot in lots:
        repository.append_event(
            db,
            event_type="relabeled",
            item_id=item_id,
            location_id=location_id,
            quantity=Decimal("0"),
            unit=lot.unit,
            lot_id=lot.id,
            expiration_date=expiration_date,
            source="api",
            actor=actor,
        )
    rebuild_projections(db)
    db.commit()
    return len(lots)


def _lot_status_event(
    db: Session, *, lot_id: UUID, event_type: str, expiration_date: date | None = None, actor: str | None = None
) -> InventoryLot:
    """Append a status-only lot event (opened/frozen/thawed/relabeled) and rebuild.
    These carry no balance change; the fold updates the lot's status/expiry."""
    lot = db.get(InventoryLot, lot_id)
    if lot is None:
        raise LookupError("Lot not found")
    repository.append_event(
        db,
        event_type=event_type,
        item_id=lot.item_id,
        location_id=lot.location_id,
        quantity=Decimal("0"),
        unit=lot.unit,
        lot_id=lot.id,
        expiration_date=expiration_date,
        source="api",
        actor=actor,
    )
    rebuild_projections(db)
    db.commit()
    return lot


def freeze_lot(db: Session, *, lot_id: UUID, actor: str | None = None) -> InventoryLot:
    return _lot_status_event(db, lot_id=lot_id, event_type="frozen", actor=actor)


def thaw_lot(db: Session, *, lot_id: UUID, actor: str | None = None) -> InventoryLot:
    return _lot_status_event(db, lot_id=lot_id, event_type="thawed", actor=actor)


def relabel_lot(db: Session, *, lot_id: UUID, expiration_date: date, actor: str | None = None) -> InventoryLot:
    """Set one lot's best-by date (the correct way to change a single lot's expiry
    without touching the others). Emits `relabeled`."""
    return _lot_status_event(db, lot_id=lot_id, event_type="relabeled", expiration_date=expiration_date, actor=actor)


def remove_lot_correction(db: Session, *, lot_id: UUID, actor: str | None = None) -> Decimal:
    """Remove a whole lot as a CORRECTION — "I added this by mistake, it was never
    really here." Emits a targeted negative ``adjusted`` (reason 'correction'), so
    it drops off the balance without counting as consumption (usage trends) OR
    waste (spoilage) — unlike Remove/Spoil. Returns the amount removed."""
    lot = db.get(InventoryLot, lot_id)
    if lot is None:
        raise LookupError("Lot not found")
    qty = Decimal(lot.quantity_remaining)
    if qty <= 0:
        raise ValueError("Nothing to remove")
    repository.append_event(
        db,
        event_type="adjusted",
        item_id=lot.item_id,
        location_id=lot.location_id,
        quantity=-qty,
        unit=lot.unit,
        lot_id=lot.id,
        source="api",
        actor=actor,
        reason="correction",
        note="removed in error",
    )
    rebuild_projections(db)
    db.commit()
    return qty


def _trim(value: Decimal) -> str:
    """Render a Decimal without trailing-zero noise: 425.200 -> 425.2, 2.000 -> 2."""
    q = value.normalize()
    return f"{q:f}"


def remove_inventory(
    db: Session,
    *,
    item_id: UUID,
    location_id: UUID,
    quantity: Decimal,
    unit: str,
    reason: str | None,
    note: str | None,
    actor: str | None = None,
    take_remainder: bool = False,
) -> tuple[InventoryTransaction, Inventory, Item]:
    item = get_item(db, item_id)
    if not item:
        raise LookupError("Item not found")

    location = get_location(db, location_id)
    if not location:
        raise LookupError("Location not found")

    # Convert the requested amount into the unit this item is already stocked in
    # at this location, so removing "1 cup" from a gallon tracked as 1 'each'
    # decrements a sixteenth — not the whole thing. The ledger sums one unit per
    # (item, location), so the event must be recorded in that stock unit.
    existing = _get_inventory_row(db, item_id=item_id, location_id=location_id)
    stock_unit = existing.unit if (existing and Decimal(existing.quantity) > 0) else unit
    amount = _convert_to_stock_unit(db, item, Decimal(quantity), from_unit=unit, to_unit=stock_unit)
    if amount is None:
        raise ValueError(
            f"Can't remove {quantity} {unit} of {item.name}: no way to convert "
            f"{unit} to the stocked unit ({stock_unit}). Set a package size, "
            f"density, or weight-per-each on the item."
        )

    row = _get_or_create_inventory_row(
        db,
        item_id=item_id,
        location_id=location_id,
        unit=stock_unit,
        expiration_date=None,
    )
    available = Decimal(row.quantity)
    if available < amount:
        if not take_remainder or available <= 0:
            # Say what was actually asked for and what is there. The bare
            # "Insufficient inventory quantity" gave no way to see that a
            # 1-'each' scan of a part-used package asks for a WHOLE package.
            # Show the original request in brackets only when it was in a
            # different unit — "99 each (99 each)" is noise.
            asked = f"{_trim(amount)} {stock_unit}"
            if unit != stock_unit:
                asked += f" ({_trim(Decimal(quantity))} {unit})"
            raise ValueError(
                f"Not enough {item.name}: asked for {asked}, but only "
                f"{_trim(available)} {stock_unit} is on hand there."
            )
        # Taking the last of a part-used package. Scanning out one 'each' of a
        # 15oz coleslaw you have already eaten half of should finish it, not
        # fail — you are standing there holding the empty tub. Never silent:
        # the caller reports the adjusted amount.
        amount = available

    row.quantity = Decimal(row.quantity) - amount
    row.unit = stock_unit

    tx = InventoryTransaction(
        item_id=item_id,
        location_id=location_id,
        quantity_change=Decimal("0") - amount,
        unit=stock_unit,
        transaction_type="remove",
        reason=reason,
        note=note,
    )
    db.add(tx)
    db.add(row)

    # Ledger: `consumed` (positive quantity; the fold applies the -1 sign).
    # Lots are allocated FEFO by the projector.
    repository.append_event(
        db,
        event_type="consumed",
        item_id=item_id,
        location_id=location_id,
        quantity=amount,
        unit=stock_unit,
        source="api",
        actor=actor,
        reason=reason,
        note=note,
    )
    rebuild_projections(db)

    db.commit()
    db.refresh(tx)
    db.refresh(row)

    get_event_bus().publish(
        InventoryRemoved(
            item_id=item_id,
            location_id=location_id,
            quantity_removed=Decimal(quantity),
            quantity_after=Decimal(row.quantity),
            unit=unit,
            reason=reason,
            note=note,
        ),
        db,
    )
    return tx, row, item


def adjust_inventory(
    db: Session,
    *,
    item_id: UUID,
    location_id: UUID,
    quantity: Decimal,
    unit: str,
    expiration_date: date | None,
    reason: str | None,
    note: str | None,
    actor: str | None = None,
) -> tuple[InventoryTransaction, Inventory, Item]:
    item = get_item(db, item_id)
    if not item:
        raise LookupError("Item not found")

    location = get_location(db, location_id)
    if not location:
        raise LookupError("Location not found")

    row = _get_or_create_inventory_row(
        db,
        item_id=item_id,
        location_id=location_id,
        unit=unit,
        expiration_date=expiration_date,
    )

    quantity_before = Decimal(row.quantity)
    delta = Decimal(quantity) - quantity_before
    row.quantity = Decimal(quantity)
    row.unit = unit
    if expiration_date is not None:
        row.expiration_date = expiration_date

    tx = InventoryTransaction(
        item_id=item_id,
        location_id=location_id,
        quantity_change=delta,
        unit=unit,
        transaction_type="adjust",
        reason=reason,
        note=note,
    )
    db.add(tx)
    db.add(row)

    # Ledger: `adjusted` carries the signed delta (negative reduces lots FEFO,
    # positive opens a new lot). Skip a no-op event when the count is unchanged.
    if delta != 0:
        repository.append_event(
            db,
            event_type="adjusted",
            item_id=item_id,
            location_id=location_id,
            quantity=delta,
            unit=unit,
            expiration_date=expiration_date if delta > 0 else None,
            source="api",
            actor=actor,
            reason=reason,
            note=note,
        )
        rebuild_projections(db)

    db.commit()
    db.refresh(tx)
    db.refresh(row)

    get_event_bus().publish(
        InventoryAdjusted(
            item_id=item_id,
            location_id=location_id,
            quantity_before=quantity_before,
            quantity_after=Decimal(row.quantity),
            unit=unit,
            reason=reason,
            note=note,
        ),
        db,
    )
    return tx, row, item


def move_inventory(
    db: Session,
    *,
    item_id: UUID,
    source_location_id: UUID,
    target_location_id: UUID,
    quantity: Decimal,
    unit: str,
    reason: str | None,
    note: str | None,
    actor: str | None = None,
) -> tuple[list[InventoryTransaction], list[tuple[Inventory, Item]]]:
    if source_location_id == target_location_id:
        raise ValueError("Source and target locations must differ")

    item = get_item(db, item_id)
    if not item:
        raise LookupError("Item not found")

    source_location = get_location(db, source_location_id)
    if not source_location:
        raise LookupError("Source location not found")

    target_location = get_location(db, target_location_id)
    if not target_location:
        raise LookupError("Target location not found")

    source_row = _get_or_create_inventory_row(
        db,
        item_id=item_id,
        location_id=source_location_id,
        unit=unit,
        expiration_date=None,
    )
    if Decimal(source_row.quantity) < Decimal(quantity):
        raise ValueError("Insufficient inventory quantity")

    target_row = _get_or_create_inventory_row(
        db,
        item_id=item_id,
        location_id=target_location_id,
        unit=unit,
        expiration_date=source_row.expiration_date,
    )

    source_row.quantity = Decimal(source_row.quantity) - Decimal(quantity)
    source_row.unit = unit
    target_row.quantity = Decimal(target_row.quantity) + Decimal(quantity)
    target_row.unit = unit
    if target_row.expiration_date is None:
        target_row.expiration_date = source_row.expiration_date

    out_tx = InventoryTransaction(
        item_id=item_id,
        location_id=source_location_id,
        quantity_change=Decimal("0") - Decimal(quantity),
        unit=unit,
        transaction_type="move_out",
        reason=reason,
        note=note,
    )
    in_tx = InventoryTransaction(
        item_id=item_id,
        location_id=target_location_id,
        quantity_change=Decimal(quantity),
        unit=unit,
        transaction_type="move_in",
        reason=reason,
        note=note,
    )

    db.add(out_tx)
    db.add(in_tx)
    db.add(source_row)
    db.add(target_row)

    # Ledger: a move is a single `transferred` fact (-source / +target). The
    # projector reduces source lots FEFO and opens a lot at the target.
    repository.append_event(
        db,
        event_type="transferred",
        item_id=item_id,
        location_id=source_location_id,
        target_location_id=target_location_id,
        quantity=Decimal(quantity),
        unit=unit,
        source="api",
        actor=actor,
        reason=reason,
        note=note,
    )
    rebuild_projections(db)

    db.commit()
    db.refresh(out_tx)
    db.refresh(in_tx)
    db.refresh(source_row)
    db.refresh(target_row)

    get_event_bus().publish(
        InventoryMoved(
            item_id=item_id,
            source_location_id=source_location_id,
            target_location_id=target_location_id,
            quantity_moved=Decimal(quantity),
            source_quantity_after=Decimal(source_row.quantity),
            target_quantity_after=Decimal(target_row.quantity),
            unit=unit,
            reason=reason,
            note=note,
        ),
        db,
    )
    return [out_tx, in_tx], [(source_row, item), (target_row, item)]


def list_transactions(db: Session, location_id: UUID | None = None, limit: int = 50) -> list[dict]:
    stmt = (
        select(InventoryTransaction, Item, InventoryLocation)
        .join(Item, InventoryTransaction.item_id == Item.id)
        .join(InventoryLocation, InventoryTransaction.location_id == InventoryLocation.id)
        .order_by(InventoryTransaction.created_at.desc())
        .limit(limit)
    )
    if location_id:
        stmt = stmt.where(InventoryTransaction.location_id == location_id)

    rows = db.execute(stmt).all()
    return [
        {
            "transaction_id": tx.id,
            "item_id": tx.item_id,
            "item_name": item.name,
            "location_id": tx.location_id,
            "location_name": location.name,
            "quantity_change": tx.quantity_change,
            "unit": tx.unit,
            "transaction_type": tx.transaction_type,
            "reason": tx.reason,
            "note": tx.note,
            "created_at": tx.created_at,
        }
        for tx, item, location in rows
    ]


def latest_location_ids(db: Session, item_ids: list[UUID]) -> dict[UUID, UUID]:
    """Where each item's most recent transaction landed — the household's own
    precedent, published for cross-module consumers (e.g. the shopping handoff
    confirmation's per-item location defaults) so they never have to read
    inventory's tables directly."""
    ids = [i for i in item_ids if i is not None]
    if not ids:
        return {}
    rows = db.execute(
        select(InventoryTransaction.item_id, InventoryTransaction.location_id, InventoryTransaction.created_at)
        .where(InventoryTransaction.item_id.in_(ids), InventoryTransaction.location_id.is_not(None))
        .order_by(InventoryTransaction.created_at.desc())
    ).all()
    latest: dict[UUID, UUID] = {}
    for item_id, location_id, _created in rows:
        if item_id not in latest:
            latest[item_id] = location_id
    return latest


# Marker note prefix on a compensating transaction: `reversal_of:<original id>`.
# There's no `reversed` flag/migration on ``InventoryTransaction`` today, so this
# marker doubles as the "already reversed" guard — see ``reverse_transaction``.
_REVERSAL_MARKER_PREFIX = "reversal_of:"


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def _originating_lot_id(db: Session, tx: InventoryTransaction) -> uuid_module.UUID | None:
    """The lot created by an ``add`` transaction's ``purchased`` event.

    The legacy transaction row doesn't reference its event, but both were
    written in the same request, so the matching purchased event is the one
    with identical identity fields whose ``occurred_at`` is nearest the
    transaction's ``created_at`` (within a tight window). Lot identity follows
    the fold's rule: the event's ``lot_id``, else uuid5("lot:{seq}")."""
    candidates = db.execute(
        select(InventoryEvent).where(
            InventoryEvent.event_type == "purchased",
            InventoryEvent.item_id == tx.item_id,
            InventoryEvent.location_id == tx.location_id,
            InventoryEvent.quantity == tx.quantity_change,
        )
    ).scalars().all()
    window = timedelta(seconds=5)
    best: tuple[timedelta, InventoryEvent] | None = None
    for ev in candidates:
        if (ev.reason or None) != (tx.reason or None) or (ev.note or None) != (tx.note or None):
            continue
        delta = abs(_as_utc(ev.occurred_at) - _as_utc(tx.created_at))
        if delta <= window and (best is None or delta < best[0]):
            best = (delta, ev)
    if best is None:
        return None
    ev = best[1]
    return ev.lot_id or uuid_module.uuid5(uuid_module.NAMESPACE_OID, f"lot:{ev.seq}")


def reverse_transaction(db: Session, transaction_id: UUID) -> dict:
    """Reverse a mistaken Log-tab entry as a compensating ledger entry (ADR 0002):
    inventory is an event-sourced ledger, so undoing a mistake is never a hard
    delete — it's a new transaction with the opposite effect, and both stay
    visible in the log.

    Only ``add`` and ``remove`` transactions are reversible: they're the two
    transaction types whose whole effect is a plain quantity change at one
    location, in one unit, undoable by the existing opposite verb. ``adjust``
    (an absolute set-to, not an add/remove) and ``move_in``/``move_out`` (two
    linked legs at different locations) don't have that one-verb inverse and are
    out of scope here — reversing those would need its own design, not a
    same-shape opposite call. A transaction that is itself a reversal (reason
    "reversal") is never reversible, so reversals can't cascade.

    Reversing an ``add`` targets the LOT that addition created (located via its
    ``purchased`` event) with a negative targeted ``adjusted`` event — the same
    mechanics as ``remove_lot_correction`` — so reversing a newer accidental
    addition can never FEFO-consume an older legitimate lot's expiry/price
    metadata. Only when the originating event can't be confidently matched
    (pre-event-log history) does it fall back to the aggregate removal path.
    Reversing a ``remove`` re-adds through ``add_inventory``. Raises
    ``LookupError`` for an unknown transaction id and ``ValueError`` for
    anything that blocks the reversal (not reversible, already reversed,
    insufficient stock left)."""
    # Row-level lock (no-op on SQLite, real on PostgreSQL): two overlapping
    # reversal requests serialize here, so the marker check below can't race —
    # the second request only proceeds after the first commits, and then sees
    # its marker.
    original = db.execute(
        select(InventoryTransaction).where(InventoryTransaction.id == transaction_id).with_for_update()
    ).scalar_one_or_none()
    if original is None:
        raise LookupError("Transaction not found")

    if original.reason == "reversal":
        raise ValueError("Cannot reverse a reversal")

    marker = f"{_REVERSAL_MARKER_PREFIX}{original.id}"
    already = db.execute(
        select(InventoryTransaction).where(InventoryTransaction.note == marker)
    ).scalar_one_or_none()
    if already is not None:
        raise ValueError("Transaction already reversed")

    amount = abs(Decimal(original.quantity_change))

    if original.transaction_type == "add":
        location = get_location(db, original.location_id)
        location_name = location.name if location else "this location"

        lot_id = _originating_lot_id(db, original)
        lot = db.get(InventoryLot, lot_id) if lot_id is not None else None
        if lot is not None and lot.unit == original.unit:
            # Targeted path: undo THIS addition's lot, not FEFO.
            remaining = Decimal(lot.quantity_remaining)
            if remaining < amount:
                raise ValueError(
                    f"Only {remaining} {original.unit} of that addition is left at {location_name}; "
                    f"can't reverse {amount} {original.unit} (some has been used since)"
                )
            row = _get_inventory_row(db, item_id=original.item_id, location_id=original.location_id)
            if row is None or Decimal(row.quantity) < amount:
                raise ValueError(
                    f"Only {Decimal(row.quantity) if row else 0} {original.unit} left at {location_name}; "
                    f"can't reverse {amount} {original.unit} (some has been used since)"
                )
            row.quantity = Decimal(row.quantity) - amount
            tx = InventoryTransaction(
                item_id=original.item_id,
                location_id=original.location_id,
                quantity_change=Decimal("0") - amount,
                unit=original.unit,
                transaction_type="remove",
                reason="reversal",
                note=marker,
            )
            db.add(tx)
            db.add(row)
            # Targeted negative `adjusted` (the remove_lot_correction mechanics):
            # reduces THAT lot and the balance without counting as consumption
            # (usage trends) or waste (spoilage).
            repository.append_event(
                db,
                event_type="adjusted",
                item_id=original.item_id,
                location_id=original.location_id,
                quantity=Decimal("0") - amount,
                unit=original.unit,
                lot_id=lot.id,
                source="api",
                reason="reversal",
                note=marker,
            )
            rebuild_projections(db)
            db.commit()
            db.refresh(tx)
        else:
            # No confident event↔transaction match (pre-event-log history) or a
            # unit drift: fall back to the aggregate removal with the on-hand
            # guard. FEFO allocation is the accepted trade-off on this path.
            row = _get_inventory_row(db, item_id=original.item_id, location_id=original.location_id)
            on_hand = Decimal(row.quantity) if row else Decimal("0")
            if on_hand < amount:
                raise ValueError(
                    f"Only {on_hand} {original.unit} left at {location_name}; "
                    f"can't reverse {amount} {original.unit} (some has been used since)"
                )
            tx, _row, _item = remove_inventory(
                db,
                item_id=original.item_id,
                location_id=original.location_id,
                quantity=amount,
                unit=original.unit,
                reason="reversal",
                note=marker,
            )
    elif original.transaction_type == "remove" and original.lot_id is not None:
        # Undo of a lot-targeted removal — a finished container, a discard, a
        # partial draw from one tub (ADR 0023 D6). The content has to go back
        # into the SAME lot: re-adding through `add_inventory` would mint a
        # fresh lot with no expiry and leave the original sitting depleted, so
        # the very container identity this ADR exists to preserve would be lost
        # by its own undo. A positive targeted `adjusted` restores that lot and
        # revives it from `depleted` in the fold.
        row = _get_inventory_row(db, item_id=original.item_id, location_id=original.location_id)
        if row is not None and normalize_unit(row.unit) == normalize_unit(original.unit):
            row.quantity = Decimal(row.quantity) + amount
            db.add(row)
        tx = InventoryTransaction(
            item_id=original.item_id,
            location_id=original.location_id,
            quantity_change=amount,
            unit=original.unit,
            transaction_type="add",
            reason="reversal",
            note=marker,
            lot_id=original.lot_id,
        )
        db.add(tx)
        repository.append_event(
            db,
            event_type="adjusted",
            item_id=original.item_id,
            location_id=original.location_id,
            quantity=amount,
            unit=original.unit,
            lot_id=original.lot_id,
            source="api",
            reason="reversal",
            note=marker,
            # Give back exactly what the removal took, container count included.
            # The original event recorded how many sealed units it dropped;
            # replaying that here is what stops a restored batch from claiming
            # fewer physical containers than it actually holds.
            payload=_original_container_delta(db, lot_id=original.lot_id, quantity=amount),
        )
        rebuild_projections(db)
        db.commit()
        db.refresh(tx)
    elif original.transaction_type == "remove":
        tx, _row, _item = add_inventory(
            db,
            item_id=original.item_id,
            location_id=original.location_id,
            quantity=amount,
            unit=original.unit,
            expiration_date=None,
            reason="reversal",
            note=marker,
        )
    else:
        raise ValueError(
            f"Transaction type '{original.transaction_type}' is not reversible "
            "(only plain add/remove entries are)"
        )

    return {"transaction_id": tx.id, "reversed_id": original.id}
