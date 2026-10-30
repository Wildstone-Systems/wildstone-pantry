"""Search-handoff shopping sessions (ADR 0019 D3 + D5).

The middle tier between "API cart" and "printable list": a guided session
PantryOS owns end to end. Items are snapshots of the pending buy list; each
row deep-links into the retailer's own site (mapping-aware via ``best_link``);
the post-shop confirmation is the purchase-history API nobody offers —
``complete`` reuses the same idempotent Purchase machinery as receipts and
CSV imports (same ledger semantics, ADR 0002/0008).
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    Purchase,
    PurchaseLineItem,
    RetailerHandoffItem,
    RetailerHandoffSession,
    ShoppingListItem,
)
from modules.shopping.retailers import CAP_SEARCH_LINK, get_adapter
from modules.shopping.services.product_maps import best_link, upsert_mapping

ITEM_STATES = {"pending", "opened", "added", "substituted", "unavailable", "skipped", "purchased"}
_ABANDON_AFTER_DAYS = 14  # housekeeping, not enforcement


def _housekeeping(db: Session) -> None:
    cutoff = datetime.now(timezone.utc) - timedelta(days=_ABANDON_AFTER_DAYS)
    stale = db.execute(
        select(RetailerHandoffSession).where(
            RetailerHandoffSession.status == "active", RetailerHandoffSession.started_at < cutoff
        )
    ).scalars().all()
    for session in stale:
        session.status = "abandoned"
        db.add(session)
    if stale:
        db.commit()


def _suggested_locations(db: Session, items: list[RetailerHandoffItem]) -> dict[UUID, UUID]:
    """Where each item's most recent inventory intake landed — the household's
    own precedent (beef patties → fridge, kidney beans → pantry), read through
    the inventory module's service interface (its tables are its own)."""
    from modules.inventory.services.inventory import latest_location_ids

    return latest_location_ids(db, [i.item_id for i in items if i.item_id is not None])


def _item_dict(db: Session, adapter, item: RetailerHandoffItem, suggested: dict[UUID, UUID] | None = None) -> dict:
    url, query = best_link(db, adapter=adapter, item_id=item.item_id, name=item.name_snapshot)
    return {
        "id": item.id,
        "shopping_list_item_id": item.shopping_list_item_id,
        "item_id": item.item_id,
        "name": item.name_snapshot,
        "quantity": item.quantity_snapshot,
        "unit": item.unit_snapshot,
        "state": item.state,
        "external_id": item.external_id,
        "external_name": item.external_name,
        "external_url": item.external_url,
        "note": item.note,
        "search_url": url,
        "search_query": query,
        "suggested_location_id": (
            suggested.get(item.item_id)
            if suggested is not None
            else _suggested_locations(db, [item]).get(item.item_id)
        )
        if item.item_id is not None
        else None,
    }


def _session_dict(db: Session, session: RetailerHandoffSession) -> dict:
    adapter = get_adapter(db, session.retailer_key)
    if adapter:
        suggested = _suggested_locations(db, list(session.items))
        items = [_item_dict(db, adapter, i, suggested=suggested) for i in session.items]
    else:
        items = []
    return {
        "id": session.id,
        "retailer_key": session.retailer_key,
        "retailer_label": adapter.label if adapter else session.retailer_key,
        "status": session.status,
        "started_at": session.started_at,
        "completed_at": session.completed_at,
        "items": items,
    }


def start_session(db: Session, *, retailer_key: str) -> dict:
    """Snapshot the pending buy list into a new handoff session."""
    _housekeeping(db)
    adapter = get_adapter(db, retailer_key)
    if adapter is None:
        raise LookupError(f"Unknown retailer: {retailer_key}")
    if CAP_SEARCH_LINK not in adapter.available(db):
        raise ValueError(f"{adapter.label} has no search handoff available.")

    from modules.shopping.services.shopping_list import list_entries

    pending = [e for e in list_entries(db) if e["status"] == "pending"]
    if not pending:
        raise ValueError("No pending items to shop for.")

    session = RetailerHandoffSession(retailer_key=retailer_key, status="active")
    db.add(session)
    db.flush()
    for entry in pending:
        db.add(
            RetailerHandoffItem(
                session_id=session.id,
                shopping_list_item_id=entry["id"],
                item_id=entry.get("item_id"),
                name_snapshot=entry["name"],
                quantity_snapshot=Decimal(str(entry["quantity"])),
                unit_snapshot=entry["unit"] or "each",
                state="pending",
            )
        )
    db.commit()
    db.refresh(session)
    return _session_dict(db, session)


def list_sessions(db: Session, *, status: str | None = "active") -> list[dict]:
    _housekeeping(db)
    stmt = select(RetailerHandoffSession).order_by(RetailerHandoffSession.started_at.desc())
    if status:
        stmt = stmt.where(RetailerHandoffSession.status == status)
    sessions = db.execute(stmt.limit(20)).scalars().all()
    return [_session_dict(db, s) for s in sessions]


def get_session(db: Session, session_id: UUID) -> dict:
    session = db.get(RetailerHandoffSession, session_id)
    if session is None:
        raise LookupError("Handoff session not found")
    return _session_dict(db, session)


def update_item(
    db: Session,
    *,
    session_id: UUID,
    handoff_item_id: UUID,
    state: str | None = None,
    external_id: str | None = None,
    external_name: str | None = None,
    external_url: str | None = None,
    note: str | None = None,
) -> dict:
    """User-driven item transitions — any state to any state; the enum is a
    vocabulary, not a straitjacket (ADR 0019 D3)."""
    item = db.get(RetailerHandoffItem, handoff_item_id)
    if item is None or item.session_id != session_id:
        raise LookupError("Handoff item not found")
    if state is not None:
        if state not in ITEM_STATES:
            raise ValueError(f"state must be one of {sorted(ITEM_STATES)}")
        item.state = state
    if external_id is not None:
        item.external_id = external_id.strip() or None
    if external_name is not None:
        item.external_name = external_name.strip() or None
    if external_url is not None:
        item.external_url = external_url.strip() or None
    if note is not None:
        item.note = note.strip() or None
    db.add(item)
    db.commit()
    db.refresh(item)
    session = db.get(RetailerHandoffSession, session_id)
    adapter = get_adapter(db, session.retailer_key)
    return _item_dict(db, adapter, item)


def complete_session(
    db: Session,
    *,
    session_id: UUID,
    purchased_item_ids: list[UUID],
    location_id: UUID | None = None,
    item_locations: dict[UUID, UUID] | None = None,
) -> dict:
    """The 30-second confirmation that replaces the purchase-history API nobody
    offers (ADR 0019 D5): mark list entries purchased, write mappings, and emit
    an idempotent Purchase. Unconfirmed items stay pending on the buy list."""
    session = db.get(RetailerHandoffSession, session_id)
    if session is None:
        raise LookupError("Handoff session not found")
    if session.status != "active":
        raise ValueError(f"Session is {session.status}, not active.")

    adapter = get_adapter(db, session.retailer_key)
    purchased_set = set(purchased_item_ids)
    known_ids = {i.id for i in session.items}
    unknown = purchased_set - known_ids
    if unknown:
        raise LookupError(f"Unknown handoff item(s): {sorted(str(u) for u in unknown)}")

    item_locations = item_locations or {}
    # Reject stale/mistyped override keys instead of silently skipping the
    # inventory intake they were meant to direct: every key must name one of
    # the item ids actually being confirmed as purchased.
    stray_keys = set(item_locations) - purchased_set
    if stray_keys:
        raise LookupError(f"item_locations references unknown purchased item(s): {sorted(str(k) for k in stray_keys)}")

    # Effective location per purchased item: a per-item override wins, else
    # the trip-level default (household feedback: beef patties → fridge,
    # kidney beans → pantry — one trip-level select doesn't fit every line).
    # Validate every referenced location up front, same as today.
    from modules.inventory.services.inventory import get_location

    referenced_ids = set(item_locations.values()) | ({location_id} if location_id is not None else set())
    for loc_id in referenced_ids:
        if get_location(db, loc_id) is None:
            raise LookupError("Location not found")

    fingerprint = f"handoff:{session.id}"
    existing = db.execute(
        select(Purchase).where(Purchase.source_fingerprint == fingerprint)
    ).scalar_one_or_none()
    if existing is not None:
        raise ValueError(f"Session already recorded (purchase_id={existing.id})")

    purchased_rows = [i for i in session.items if i.id in purchased_set]

    # Mappings: every confirmed pick with a store identity teaches the map
    # (source=handoff) — this is the compounding write the whole design feeds.
    for item in session.items:
        if item.item_id and item.external_id and item.state in ("added", "substituted", "purchased"):
            upsert_mapping(
                db,
                item_id=item.item_id,
                retailer_key=session.retailer_key,
                external_id=item.external_id,
                source="handoff",
                external_name=item.external_name,
                external_url=item.external_url,
                commit=False,
            )

    # The trip record (purchase + lines + mappings + states + session close) is
    # committed as ONE transaction BEFORE any inventory write. add_inventory()
    # commits internally per item, so interleaving it with the record would let
    # a mid-loop failure strand a half-committed trip whose fingerprint blocks
    # retrying. This order means completion either fully records or fully rolls
    # back; a later inventory failure surfaces loudly but never wedges the
    # session (same exposure as the existing CSV/receipt import paths).
    purchase = None
    inventory_plan: list[tuple] = []  # (item_id, location_id, quantity, unit, expiration_date, note)
    if purchased_rows:
        purchase = Purchase(
            merchant=adapter.label if adapter else session.retailer_key,
            source=f"handoff:{session.retailer_key}",
            purchased_on=date.today(),
            location_id=location_id,
            source_fingerprint=fingerprint,
            status="processing",
        )
        db.add(purchase)
        db.flush()

        from modules.automation import ExpirationEstimator
        from modules.catalog.services.items import get_item

        estimator = ExpirationEstimator()
        for row in purchased_rows:
            row.state = "purchased"
            db.add(row)
            display_name = row.external_name or row.name_snapshot
            item = get_item(db, row.item_id) if row.item_id else None
            estimated_days = (item.default_expiration_days if item else None) or estimator.estimate(
                display_name, None
            )
            effective_location_id = item_locations.get(row.id) or location_id
            if effective_location_id is not None and item is not None:
                inventory_plan.append(
                    (
                        item.id,
                        effective_location_id,
                        Decimal(row.quantity_snapshot),
                        row.unit_snapshot,
                        date.today() + timedelta(days=int(estimated_days)),
                        f"purchase:{purchase.id}",
                    )
                )
            db.add(
                PurchaseLineItem(
                    purchase_id=purchase.id,
                    raw_line=f"{row.name_snapshot},{row.quantity_snapshot},{row.unit_snapshot}",
                    parsed_name=display_name,
                    parsed_quantity=Decimal(row.quantity_snapshot),
                    parsed_unit=row.unit_snapshot,
                    matched_item_id=row.item_id,
                    expiration_days_estimate=int(estimated_days),
                )
            )
            # Close the loop on the live buy list.
            if row.shopping_list_item_id:
                entry = db.get(ShoppingListItem, row.shopping_list_item_id)
                if entry is not None and entry.status == "pending":
                    entry.status = "purchased"
                    db.add(entry)

        purchase.status = "processed"
        purchase.line_count = len(purchased_rows)
        db.add(purchase)

    session.status = "completed"
    session.completed_at = datetime.now(timezone.utc)
    db.add(session)
    db.commit()

    if inventory_plan:
        from modules.inventory.services.inventory import add_inventory

        for item_id, item_location_id, quantity, unit, expiration_date, note in inventory_plan:
            add_inventory(
                db,
                item_id=item_id,
                location_id=item_location_id,
                quantity=quantity,
                unit=unit,
                expiration_date=expiration_date,
                reason="purchase_import",
                note=note,
            )

    db.refresh(session)

    result = _session_dict(db, session)
    result["purchase_id"] = purchase.id if purchase else None
    result["purchased_count"] = len(purchased_rows)
    return result
