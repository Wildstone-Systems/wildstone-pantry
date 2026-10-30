"""Household shopping (buy) list — step 3 (close the loop).

Auto entries are reconciled from catalog restock thresholds against on-hand read
from the event-sourced ``inv_balance`` projection (ADR 0002), summed across all
locations. Manual entries are independent and never auto-removed. Reconciliation
runs on read so the list never drifts from the ledger.
"""

from __future__ import annotations

import hashlib
from decimal import ROUND_CEILING, ROUND_HALF_UP, Decimal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from datetime import datetime, timezone

from app.models import Item, ShoppingListItem
from modules.catalog.schemas.item import ItemCreate
from modules.catalog.services.barcodes import find_item_by_name, lookup_barcode, normalize_barcode, rescan_item, resolve_local_item
from modules.catalog.services.items import create_item, get_item
from modules.catalog.services.units import convert_quantity, net_contents_in_base, normalize_unit
from modules.inventory.models import InventoryBalance
from modules.inventory.services.feed import on_hand_by_item as inventory_on_hand
from modules.shopping import repository
from modules.shopping.connectors import ExportLine, ExportResult, get_target, list_targets


def _on_hand_by_item(db: Session) -> dict[UUID, Decimal]:
    # Inventory owns the balances; read them through its published service.
    return inventory_on_hand(db)


def _pending_item_ids(db: Session) -> set[UUID]:
    rows = db.execute(
        select(ShoppingListItem.item_id).where(
            ShoppingListItem.status == "pending", ShoppingListItem.item_id.is_not(None)
        )
    ).all()
    return {r[0] for r in rows}


def reconcile_auto_entries(db: Session) -> None:
    """Add auto entries for items that dropped below their restock threshold;
    remove auto *pending* entries whose item has recovered (or lost its
    threshold). Manual and purchased entries are left untouched."""
    _reconcile_auto_entries(db)
    db.commit()


def _reconcile_auto_entries(db: Session) -> None:
    """``reconcile_auto_entries`` inside the caller's transaction: the caller
    commits (or rolls back) along with its own writes, and holds the lock
    until then, so two reconciles never both add the same item."""
    repository.lock_auto_entries(db)
    on_hand = _on_hand_by_item(db)
    tracked = repository.items_with_restock_threshold(db)

    below: dict[UUID, tuple[Item, Decimal]] = {}
    for item in tracked:
        threshold = Decimal(item.restock_threshold)
        have = on_hand.get(item.id, Decimal("0"))
        if have < threshold:
            below[item.id] = (item, threshold - have)

    existing_pending = _pending_item_ids(db)
    # Items the user explicitly dismissed from the auto-list — leave them off
    # until their stock recovers (below), so a delete actually sticks.
    dismissed_ids = {
        r[0]
        for r in db.execute(
            select(ShoppingListItem.item_id).where(
                ShoppingListItem.source == "auto",
                ShoppingListItem.status == "dismissed",
                ShoppingListItem.item_id.is_not(None),
            )
        ).all()
    }

    # Add auto entries for newly-below items that aren't already on the list
    # (and weren't dismissed).
    for item_id, (item, needed) in below.items():
        if item_id in existing_pending or item_id in dismissed_ids:
            continue
        unit = db.execute(
            select(InventoryBalance.unit).where(InventoryBalance.item_id == item_id).limit(1)
        ).scalar_one_or_none()
        # Buy by the PACKAGE, not the base unit (ADR 0016): on-hand/threshold are in
        # base units (20 pouches, 47 g, 1893 ml), but the shopping list and cart must
        # order whole packages — else "3 pouches short" orders 3 packs = 60 pouches,
        # or "50 g short" orders 50 jars. Convert the base shortfall up to packages.
        qty = needed if needed > 0 else Decimal("1")
        entry_unit = unit or "each"
        per_pack = net_contents_in_base(
            item.base_unit,
            item.stock_mode,
            item.size_quantity,
            item.size_unit,
            density_g_per_ml=(Decimal(str(item.density_g_per_ml)) if item.density_g_per_ml is not None else None),
            grams_per_each=(Decimal(str(item.grams_per_each)) if item.grams_per_each is not None else None),
        )
        if per_pack and per_pack > 0:
            qty = (qty / per_pack).to_integral_value(rounding=ROUND_CEILING)
            entry_unit = "each"  # a package
        qty = max(qty, Decimal("1"))
        db.add(
            ShoppingListItem(
                item_id=item_id,
                name=item.name,
                quantity=qty,
                unit=entry_unit,
                source="auto",
                status="pending",
            )
        )

    # Clear auto entries (pending OR dismissed) whose item recovered / lost its
    # threshold — a recovered item drops its dismissal so it can trigger again.
    auto_entries = db.execute(
        select(ShoppingListItem).where(
            ShoppingListItem.source == "auto",
            ShoppingListItem.status.in_(("pending", "dismissed")),
        )
    ).scalars().all()
    for entry in auto_entries:
        if entry.item_id is None or entry.item_id not in below:
            db.delete(entry)
    db.flush()


def list_state(db: Session) -> dict:
    """What the grocery list looks like right now, for noticing a change
    (ADR 0026 §4, ``pantry.shopping_list_changed``).

    ``fingerprint`` changes whenever an entry is added, edited, checked off or
    removed; ``changed_at`` is the newest edit, so a caller can wait for a
    burst of edits to settle; ``pending`` is how many entries are on the list.

    Automatic entries are brought up to date first, inside the caller's
    transaction, so an item dropping below its restock threshold is a list
    change even when nobody has opened the list (#83).
    """
    _reconcile_auto_entries(db)
    rows = repository.pending_fingerprint_rows(db)
    digest = hashlib.sha256()
    for entry_id, name, quantity, unit, updated_at in rows:
        digest.update(f"{entry_id}|{name}|{quantity}|{unit}|{updated_at}\n".encode())
    return {
        "fingerprint": digest.hexdigest()[:32],
        "pending": len(rows),
        "changed_at": repository.latest_list_change(db),
    }


def items_below_threshold(db: Session) -> list[dict]:
    """Items whose total on-hand is under their restock threshold — the same
    rule that drives auto entries. Published for cross-module consumers (agent
    summary ADR 0011, Wildstone ``pantry.stock_low`` ADR 0026)."""
    on_hand = _on_hand_by_item(db)
    tracked = repository.items_with_restock_threshold(db)
    out = []
    for item in tracked:
        have = on_hand.get(item.id, Decimal("0"))
        threshold = Decimal(item.restock_threshold)
        if have < threshold:
            out.append(
                {"item_id": item.id, "name": item.name, "on_hand": have, "threshold": threshold, "unit": item.base_unit}
            )
    return out


def count_items_below_threshold(db: Session) -> int:
    return len(items_below_threshold(db))


def _to_dict(entry: ShoppingListItem, on_hand: dict[UUID, Decimal]) -> dict:
    return {
        "id": entry.id,
        "item_id": entry.item_id,
        "name": entry.name,
        "quantity": entry.quantity,
        "unit": entry.unit,
        "source": entry.source,
        "status": entry.status,
        "note": entry.note,
        "chosen_offer": entry.chosen_offer,
        "on_hand": on_hand.get(entry.item_id) if entry.item_id else None,
        "created_at": entry.created_at,
        "updated_at": entry.updated_at,
    }


def list_entries(db: Session, *, refresh: bool = True) -> list[dict]:
    if refresh:
        reconcile_auto_entries(db)
    on_hand = _on_hand_by_item(db)
    # Pending first, then purchased; alphabetical within each. Dismissed auto
    # entries are hidden (they exist only to suppress re-adding until recovery).
    rows = db.execute(
        select(ShoppingListItem)
        .where(ShoppingListItem.status != "dismissed")
        .order_by((ShoppingListItem.status == "purchased"), ShoppingListItem.name.asc())
    ).scalars().all()
    return [_to_dict(e, on_hand) for e in rows]


def add_manual_entry(
    db: Session,
    *,
    name: str,
    item_id: UUID | None = None,
    quantity: Decimal = Decimal("1"),
    unit: str = "each",
    note: str | None = None,
    source: str = "manual",
) -> dict:
    # Idempotent for a linked item: if it's already pending on the list (auto or
    # manual), return that entry instead of creating a duplicate — so "add from
    # Running Low" can't stack the same item.
    if item_id is not None:
        existing = db.execute(
            select(ShoppingListItem).where(
                ShoppingListItem.item_id == item_id,
                ShoppingListItem.status == "pending",
            )
        ).scalars().first()
        if existing is not None:
            return _to_dict(existing, _on_hand_by_item(db))

    entry = ShoppingListItem(
        item_id=item_id,
        name=name.strip(),
        quantity=quantity,
        unit=unit,
        source=source,
        status="pending",
        note=note,
    )
    db.add(entry)
    db.commit()
    db.refresh(entry)
    return _to_dict(entry, _on_hand_by_item(db))


def _resolve_offer_to_item(db: Session, offer: dict) -> UUID | None:
    """Create/find the catalog item a chosen offer refers to (ADR 0013).

    A UPC is enriched through the normal scan path (full OFF/USDA data); if that
    yields nothing, or there's no UPC, fall back to a minimal item from the
    offer fields. Never creates a duplicate barcode."""
    offer_size = (offer.get("size") or "").strip() or None
    offer_image = (offer.get("image_url") or "").strip() or None
    upc = (offer.get("upc") or "").strip()
    if upc:
        result = lookup_barcode(db, upc, auto_create=True)
        if result.get("item_id"):
            item = get_item(db, result["item_id"])
            # The offer often has data the external provider lacked (e.g. a Kroger
            # size/image for a UPC OFF has nothing for) — fill the gaps only.
            if item is not None:
                changed = False
                if offer_size and not item.size_description:
                    item.size_description, changed = offer_size, True
                if offer_image and not item.image_url:
                    item.image_url, changed = offer_image, True
                if changed:
                    db.add(item)
                    db.commit()
            return result["item_id"]

    payload = ItemCreate(
        name=(offer.get("name") or "Unnamed item").strip()[:200],
        brand=(offer.get("brand") or None),
        barcode=upc or None,
        size_description=offer_size,
        image_url=offer_image,
    )
    try:
        return create_item(db, payload).id
    except Exception:
        db.rollback()
        # A racing/duplicate barcode: fall back to the existing item for that UPC.
        if upc:
            existing = lookup_barcode(db, upc, auto_create=False)
            if existing.get("item_id"):
                return existing["item_id"]
        raise


def _enrich_item_from_offer(db: Session, item: Item, offer: dict) -> None:
    """Fill an existing item's gaps from a chosen offer (never overwrites).

    Lets shopping for a recipe's ingredient attach the store's UPC/brand/size/
    image to the *existing* item instead of spawning a duplicate."""
    changed = False
    upc = (offer.get("upc") or "").strip()
    if upc and not (item.barcode or "").strip():
        # Only claim the UPC if no other item already owns it (unique barcode).
        owner = resolve_local_item(db, upc)
        if owner is None or owner.id == item.id:
            item.barcode = normalize_barcode(upc)
            changed = True
    if (offer.get("brand") or "").strip() and not (item.brand or "").strip():
        item.brand = offer["brand"].strip()
        changed = True
    if (offer.get("size") or "").strip() and not (item.size_description or "").strip():
        item.size_description = offer["size"].strip()
        changed = True
    if (offer.get("image_url") or "").strip() and not (item.image_url or "").strip():
        item.image_url = offer["image_url"].strip()
        changed = True
    if changed:
        db.add(item)
        db.commit()


def select_offer(
    db: Session,
    *,
    offer: dict,
    quantity: Decimal = Decimal("1"),
    unit: str = "each",
    entry_id: UUID | None = None,
    item_id: UUID | None = None,
) -> dict:
    """Resolve a shopping need to a concrete offer: link/enrich the catalog item
    and record the chosen-offer snapshot. Updates an existing entry when
    ``entry_id`` is given, otherwise adds a new resolved entry (ADR 0013).

    ``item_id`` attaches the offer to an existing catalog item (enriching it)
    instead of creating a new one — the fix for recipe-driven shopping spawning
    duplicate items."""
    if item_id is not None:
        target = get_item(db, item_id)
        if target is None:
            raise LookupError("Item not found")
        _enrich_item_from_offer(db, target, offer)
        # If the offer supplied a UPC, also pull nutrition/serving/density from the
        # provider cascade (OFF→USDA→Kroger), same as the by-UPC path does. Without
        # this a recipe-shopped ingredient ends up with a barcode but no nutrition —
        # the inconsistency where only some catalog items had macros/units.
        if (target.barcode or "").strip() and target.nutrition is None:
            try:
                rescan_item(db, target.id)
            except Exception:
                db.rollback()  # provider down / network error — enrichment is best-effort
            db.refresh(target)
        resolved_item_id = target.id
    else:
        resolved_item_id = _resolve_offer_to_item(db, offer)

    snapshot = dict(offer)
    snapshot["chosen_at"] = datetime.now(timezone.utc).isoformat()

    if entry_id is not None:
        entry = db.get(ShoppingListItem, entry_id)
        if not entry:
            raise LookupError("Shopping list entry not found")
        entry.item_id = resolved_item_id
        entry.name = (offer.get("name") or entry.name).strip()[:200]
        entry.chosen_offer = snapshot
    else:
        entry = ShoppingListItem(
            item_id=resolved_item_id,
            name=(offer.get("name") or "Unnamed item").strip()[:200],
            quantity=quantity,
            unit=unit,
            source="manual",
            status="pending",
            chosen_offer=snapshot,
        )
        db.add(entry)

    db.commit()
    db.refresh(entry)

    # Picking an offer is a mapping confirmation (ADR 0019 D4, source=offer_pick):
    # remember "this item at this retailer is this product" for future trips.
    external_id = ((offer.get("vendor_sku") or offer.get("upc")) or "").strip()
    if entry.item_id is not None and (offer.get("provider") or "").strip() and external_id:
        from modules.shopping.services.product_maps import upsert_mapping

        upsert_mapping(
            db,
            item_id=entry.item_id,
            retailer_key=offer["provider"].strip(),
            external_id=external_id,
            source="offer_pick",
            external_name=(offer.get("name") or "").strip() or None,
            upc=(offer.get("upc") or "").strip() or None,
            size=(offer.get("size") or "").strip() or None,
        )

    return _to_dict(entry, _on_hand_by_item(db))


def _pending_by_name(db: Session, name: str) -> ShoppingListItem | None:
    return repository.pending_by_name(db, name)


def add_entry_once(db: Session, *, name: str, source: str = "manual", note: str | None = None) -> tuple[dict, bool]:
    """Put ``name`` on the list unless it is already pending there.

    Returns ``(entry, added)``. Matching is by case-insensitive name, and by the
    catalog item when the name is a known one — so a routine that fires twice,
    or "milk" said twice, never puts milk on the list twice (ADR 0026 §2).
    """
    # Held until the add below commits: a second identical add waits here,
    # then finds this one's entry instead of inserting its own.
    repository.lock_repeat_safe_adds(db)
    existing = _pending_by_name(db, name)
    if existing is not None:
        db.commit()  # release the lock
        return _to_dict(existing, _on_hand_by_item(db)), False
    item = find_item_by_name(db, name.strip())
    if item is not None:
        pending_ids = _pending_item_ids(db)
        entry = add_manual_entry(db, name=item.name, item_id=item.id, source=source, note=note)
        return entry, item.id not in pending_ids
    return add_manual_entry(db, name=name, source=source, note=note), True


def check_off_by_name(db: Session, *, name: str) -> dict | None:
    """Mark the pending entry called ``name`` as bought; None if it isn't pending."""
    existing = _pending_by_name(db, name)
    if existing is None:
        return None
    return update_entry(db, entry_id=existing.id, status="purchased")


def update_entry(
    db: Session,
    *,
    entry_id: UUID,
    quantity: Decimal | None = None,
    status: str | None = None,
    note: str | None = None,
    note_set: bool = False,
) -> dict:
    entry = db.get(ShoppingListItem, entry_id)
    if entry is None:
        raise LookupError("Shopping list entry not found")
    if quantity is not None:
        entry.quantity = quantity
    if status is not None:
        if status not in ("pending", "purchased"):
            raise ValueError("status must be pending or purchased")
        entry.status = status
    if note_set:
        entry.note = note
    db.add(entry)
    db.commit()
    db.refresh(entry)
    return _to_dict(entry, _on_hand_by_item(db))


def delete_entry(db: Session, *, entry_id: UUID) -> None:
    entry = db.get(ShoppingListItem, entry_id)
    if entry is None:
        raise LookupError("Shopping list entry not found")
    # Deleting an AUTO entry dismisses it rather than hard-deleting: otherwise the
    # restock reconciler re-adds it while the item is still below threshold (the
    # "can't remove it" bug). It stays off the list until the item's stock
    # recovers, then can trigger again. Manual/purchased entries delete outright.
    if entry.source == "auto" and entry.status == "pending":
        entry.status = "dismissed"
        db.add(entry)
    else:
        db.delete(entry)
    db.commit()


# ---- Vendor export (step 5) ------------------------------------------------


def _kroger_upc(raw: str | None) -> str | None:
    """Normalise a code to the exactly-13-digit GTIN Kroger's cart API requires
    (CART-2012). Zero-pads shorter codes; strips a leading zero off a GTIN-14.
    Returns None when it can't be made to fit 13 digits."""
    digits = "".join(ch for ch in (raw or "") if ch.isdigit())
    if not digits:
        return None
    if len(digits) <= 13:
        return digits.zfill(13)
    if len(digits) == 14 and digits[0] == "0":
        return digits[1:]
    return None


# Units where the entry quantity is already a package/pack count, not a measure.
_COUNT_UNITS = {"each", "ea", "ct", "pack", "pk", "unit", ""}


def _cart_quantity(entry: dict, item: Item | None) -> int:
    """How many PACKAGES to order for a shopping entry.

    A count-unit entry ('1 each', '2 ct') is already a package count. A *measured*
    entry — a recipe amount like '28 oz crushed tomatoes' — is divided by the
    item's package size to get packages ('28 oz' ÷ a 28 oz can = 1), rounding up.
    Falls back to a single package when a measured amount can't be converted
    (missing size/density) — never the raw measure, so '28 oz' can't become 28."""
    try:
        qty = Decimal(str(entry["quantity"]))
    except (TypeError, ValueError, KeyError):
        return 1
    unit = normalize_unit(entry.get("unit") or "")
    if unit in _COUNT_UNITS:
        return max(1, int(qty.to_integral_value(rounding=ROUND_HALF_UP)))

    if item is not None and item.size_quantity and item.size_unit:
        pack_qty = Decimal(str(item.size_quantity))
        need_in_pack_unit = convert_quantity(
            qty,
            unit,
            item.size_unit,
            density_g_per_ml=(Decimal(str(item.density_g_per_ml)) if item.density_g_per_ml is not None else None),
            grams_per_each=(Decimal(str(item.grams_per_each)) if item.grams_per_each is not None else None),
        )
        if need_in_pack_unit is not None and pack_qty > 0:
            packages = (need_in_pack_unit / pack_qty).to_integral_value(rounding=ROUND_CEILING)
            return max(1, int(packages))

    return 1  # measured but not convertible → assume one package


def kroger_cart_items(db: Session) -> tuple[list[dict], list[dict]]:
    """Split the pending buy list into (orderable, skipped) for the Kroger cart.

    Orderable entries were matched to a store product (a ``chosen_offer`` with a
    UPC) and normalise to a 13-digit GTIN. Everything else — auto entries with no
    offer, loose needs, un-normalisable codes — is reported as skipped so the
    export never fails on them."""
    orderable: list[dict] = []
    skipped: list[dict] = []
    for entry in list_entries(db):
        if entry["status"] != "pending":
            continue
        offer = entry.get("chosen_offer") or {}
        upc = _kroger_upc(offer.get("upc"))
        if not upc:
            reason = "no store product match — search and pick one to order it"
            skipped.append({"name": entry["name"], "reason": reason})
            continue
        item = get_item(db, entry["item_id"]) if entry.get("item_id") else None
        qty_int = _cart_quantity(entry, item)
        orderable.append({"upc": upc, "quantity": qty_int, "name": entry["name"], "item_id": entry.get("item_id")})
    return orderable, skipped


def push_to_kroger_cart(db: Session) -> dict:
    """Add the pending buy list to the user's Kroger cart (Phase 4 + ADR 0018 §3).

    BYO local credentials keep the original path; with none, a brokered
    connection (PantryOS Connect) supplies short-lived access tokens instead.
    Raises ValueError when neither is available; KrogerNotConnected if no
    account is linked; KrogerCartError on an API failure;
    BrokerEntitlementLapsed when the subscription behind a brokered
    connection is inactive. Returns added vs skipped plus the cart URL."""
    from app.services import broker_connections, provider_keys
    from modules.shopping.services import kroger_cart as kc

    orderable, skipped = kroger_cart_items(db)
    items = [kc.CartItem(upc=row["upc"], quantity=row["quantity"]) for row in orderable]

    client_id, client_secret = provider_keys.kroger_credentials(db)
    if client_id and client_secret:
        rejected = kc.add_to_cart(db, items, client_id=client_id, client_secret=client_secret)
    elif broker_connections.get_connection(db, "kroger") is not None:

        def _brokered_token(force: bool) -> str:
            return broker_connections.access_token(db, "kroger", force=force)

        rejected = kc.add_to_cart_via(items, get_token=_brokered_token)
    else:
        raise ValueError("Kroger client credentials are not configured.")

    # A UPC Kroger refused (e.g. not in its catalog) is reported, not fatal.
    rejected_upcs = {r["upc"] for r in rejected}
    by_upc = {row["upc"]: row for row in orderable}
    failed = [{"name": by_upc.get(r["upc"], {}).get("name", r["upc"]), "reason": r["reason"]} for r in rejected]
    added = [row for row in orderable if row["upc"] not in rejected_upcs]

    # Every UPC Kroger accepted is a mapping confirmation (ADR 0019 D4,
    # source=cart_push) — the store's own catalog just validated the match.
    from modules.shopping.services.product_maps import upsert_mapping

    for row in added:
        if row.get("item_id"):
            upsert_mapping(
                db,
                item_id=row["item_id"],
                retailer_key="kroger",
                external_id=row["upc"],
                source="cart_push",
                external_name=row["name"],
                upc=row["upc"],
            )

    return {"added": added, "skipped": skipped, "failed": failed, "cart_url": "https://www.kroger.com/cart"}


def instacart_line_items(db: Session) -> list[dict]:
    """Build Instacart shoppable-list line-items from the pending buy list.

    Every pending entry is included by NAME (Instacart matches products itself),
    with a UPC attached when we have one (Instacart prioritises it). Unlike the
    Kroger cart, nothing is skipped — name matching covers the no-UPC items."""
    line_items: list[dict] = []
    for entry in list_entries(db):
        if entry["status"] != "pending":
            continue
        li: dict = {
            "name": entry["name"],
            "quantity": float(entry["quantity"]),
            "unit": entry["unit"] or "each",
        }
        upc = ((entry.get("chosen_offer") or {}).get("upc") or "").strip()
        if upc:
            li["upcs"] = [upc]
        line_items.append(li)
    return line_items


def create_instacart_page(db: Session) -> dict:
    """Turn the pending buy list into an Instacart shoppable-list link (ADR 0013
    Phase 4b). Dormant until an Instacart API key is configured.

    Raises NotConfiguredError (no key), ValueError (empty list), or InstacartError
    (API failure). Returns the link URL and how many items it covers."""
    from app.services import provider_keys
    from modules.shopping.connectors import instacart as ic

    api_key = provider_keys.instacart_api_key(db)
    if not api_key:
        from modules.shopping.connectors import NotConfiguredError

        raise NotConfiguredError(
            "Instacart isn't configured — add a Developer Platform API key under Integrations."
        )

    line_items = instacart_line_items(db)
    if not line_items:
        raise ValueError("No pending items to send to Instacart.")

    url = ic.create_products_link(
        title="PantryOS shopping list",
        line_items=line_items,
        api_key=api_key,
        base_url=provider_keys.instacart_base_url(db),
    )
    return {"url": url, "count": len(line_items)}


def available_targets() -> list[dict]:
    """The registered export targets + whether each is configured (dormant
    vendors like Kroger report configured=false until credentials are set)."""
    return [{"key": t.key, "label": t.label, "configured": t.configured()} for t in list_targets()]


def export_shopping_list(db: Session, *, target_key: str) -> ExportResult:
    """Build an export of the *pending* buy list for the given target."""
    target = get_target(target_key)
    if target is None:
        raise LookupError(f"Unknown export target: {target_key}")
    lines = [
        ExportLine(name=e["name"], quantity=Decimal(str(e["quantity"])), unit=e["unit"])
        for e in list_entries(db)
        if e["status"] == "pending"
    ]
    return target.export(lines)
