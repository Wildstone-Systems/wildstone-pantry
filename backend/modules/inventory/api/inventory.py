from datetime import date
from decimal import Decimal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.security import allow_kiosk, resolve_actor
from app.db import get_db
from app.models import InventoryTransaction
from app.schemas import (
    InventoryAddRequest,
    InventoryAdjustRequest,
    InventoryByLocationResponse,
    InventoryEventListResponse,
    InventoryEventRead,
    InventoryLineRead,
    InventoryMoveRequest,
    InventoryMutationResponse,
    InventoryRemoveRequest,
    InventoryScanRequest,
    InventoryScanResponse,
    TransactionListResponse,
    TransactionRead,
    TransactionReverseResponse,
)
from app.services import (
    add_inventory,
    adjust_inventory,
    get_events,
    get_inventory_by_location,
    get_location,
    list_transactions,
    move_inventory,
    remove_inventory,
    scan_move,
)
from modules.inventory.schemas.inventory import (
    ContainerCreate,
    ContainerListResponse,
    ContainerRead,
    DiscardContainerRequest,
    LotActionResponse,
    LotConsumeRequest,
    LotListResponse,
    LotRead,
    LotRelabelRequest,
    OpenContainerRequest,
    ScanContainerOption,
    ScanLocationOption,
    SetExpirationRequest,
    SetExpirationResponse,
    WorklistResponse,
    WorklistRow,
)
from modules.inventory.services import containers as container_service
from modules.inventory.services.worklist import build_worklist
from modules.inventory.services.inventory import (
    balance_line,
    consume_container,
    discard_container,
    freeze_lot,
    list_lots,
    lot_identity,
    open_container,
    plan_container_scan_out,
    relabel_lot,
    remove_lot_correction,
    resolve_scan,
    reverse_transaction,
    scan_location_candidates,
    set_item_expiration,
    thaw_lot,
)

router = APIRouter(prefix="/inventory", tags=["inventory"], dependencies=[Depends(allow_kiosk)])


@router.get("", response_model=InventoryByLocationResponse)
def get_inventory_route(location_id: UUID, db: Session = Depends(get_db)) -> InventoryByLocationResponse:
    location = get_location(db, location_id)
    if not location:
        raise HTTPException(status_code=404, detail="Location not found")

    inventory_rows = get_inventory_by_location(db, location_id)

    return InventoryByLocationResponse(
        location_id=location_id,
        location_name=location.name,
        items=[InventoryLineRead.model_validate(row) for row in inventory_rows],
    )


@router.post("/add", response_model=InventoryMutationResponse)
def add_inventory_route(
    payload: InventoryAddRequest,
    db: Session = Depends(get_db),
    actor: str | None = Depends(resolve_actor),
) -> InventoryMutationResponse:
    # Container intake (ADR 0014 × 0016): add_inventory now converts a container
    # batch to base units itself (per-container size × sealed count), so the route
    # just forwards the fields — no pre-multiply here.
    try:
        tx, row, item = add_inventory(
            db,
            item_id=payload.item_id,
            location_id=payload.location_id,
            quantity=payload.quantity,
            unit=payload.unit,
            expiration_date=payload.expiration_date,
            reason=payload.reason,
            note=payload.note,
            actor=actor,
            container_id=payload.container_id,
            container_size=payload.container_size,
            container_unit=payload.container_unit,
            sealed_count=payload.sealed_count,
            unit_price=payload.unit_price,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return InventoryMutationResponse(
        transaction_ids=[tx.id],
        inventory=[
            InventoryLineRead.model_validate(
                {
                    "inventory_id": row.id,
                    "item_id": row.item_id,
                    "item_name": item.name,
                    "location_id": row.location_id,
                    "quantity": row.quantity,
                    "unit": row.unit,
                    "expiration_date": row.expiration_date,
                    "updated_at": row.updated_at,
                }
            )
        ],
        # Base-unit delta for the client toast ("Added 2 × 0.5 gal (3.79 L)").
        added_quantity=tx.quantity_change,
        added_unit=row.unit,
    )


@router.post("/remove", response_model=InventoryMutationResponse)
def remove_inventory_route(
    payload: InventoryRemoveRequest,
    db: Session = Depends(get_db),
    actor: str | None = Depends(resolve_actor),
) -> InventoryMutationResponse:
    try:
        tx, row, item = remove_inventory(
            db,
            item_id=payload.item_id,
            location_id=payload.location_id,
            quantity=payload.quantity,
            unit=payload.unit,
            reason=payload.reason,
            note=payload.note,
            actor=actor,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return InventoryMutationResponse(
        transaction_ids=[tx.id],
        inventory=[
            InventoryLineRead.model_validate(
                {
                    "inventory_id": row.id,
                    "item_id": row.item_id,
                    "item_name": item.name,
                    "location_id": row.location_id,
                    "quantity": row.quantity,
                    "unit": row.unit,
                    "expiration_date": row.expiration_date,
                    "updated_at": row.updated_at,
                }
            )
        ],
    )


@router.post("/adjust", response_model=InventoryMutationResponse)
def adjust_inventory_route(
    payload: InventoryAdjustRequest,
    db: Session = Depends(get_db),
    actor: str | None = Depends(resolve_actor),
) -> InventoryMutationResponse:
    try:
        tx, row, item = adjust_inventory(
            db,
            item_id=payload.item_id,
            location_id=payload.location_id,
            quantity=payload.quantity,
            unit=payload.unit,
            expiration_date=payload.expiration_date,
            reason=payload.reason,
            note=payload.note,
            actor=actor,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    return InventoryMutationResponse(
        transaction_ids=[tx.id],
        inventory=[
            InventoryLineRead.model_validate(
                {
                    "inventory_id": row.id,
                    "item_id": row.item_id,
                    "item_name": item.name,
                    "location_id": row.location_id,
                    "quantity": row.quantity,
                    "unit": row.unit,
                    "expiration_date": row.expiration_date,
                    "updated_at": row.updated_at,
                }
            )
        ],
    )


@router.post("/move", response_model=InventoryMutationResponse)
def move_inventory_route(
    payload: InventoryMoveRequest,
    db: Session = Depends(get_db),
    actor: str | None = Depends(resolve_actor),
) -> InventoryMutationResponse:
    try:
        tx_rows, inventory_rows = move_inventory(
            db,
            item_id=payload.item_id,
            source_location_id=payload.source_location_id,
            target_location_id=payload.target_location_id,
            quantity=payload.quantity,
            unit=payload.unit,
            reason=payload.reason,
            note=payload.note,
            actor=actor,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    normalized = []
    for row, item in inventory_rows:
        normalized.append(
            InventoryLineRead.model_validate(
                {
                    "inventory_id": row.id,
                    "item_id": row.item_id,
                    "item_name": item.name,
                    "location_id": row.location_id,
                    "quantity": row.quantity,
                    "unit": row.unit,
                    "expiration_date": row.expiration_date,
                    "updated_at": row.updated_at,
                }
            )
        )

    return InventoryMutationResponse(
        transaction_ids=[tx.id for tx in tx_rows],
        inventory=normalized,
    )


@router.get("/item-locations", response_model=list[ScanLocationOption])
def item_locations_route(
    item_id: UUID,
    direction: str = Query(pattern="^(in|out)$"),
    db: Session = Depends(get_db),
) -> list[ScanLocationOption]:
    """Where an item should land for a location-less pick (log-a-meal, or any
    other "auto-find the location" flow that isn't itself a barcode scan).
    Same resolution scan-station scans already use: for ``out`` this is every
    location it's actually in stock (one candidate means the caller can use it
    with no prompt); for ``in`` it's where it already lives, or every location
    if it's never been stocked anywhere."""
    return [ScanLocationOption(**c) for c in scan_location_candidates(db, item_id, direction)]


@router.post("/scan", response_model=InventoryScanResponse)
def scan_route(
    payload: InventoryScanRequest,
    db: Session = Depends(get_db),
    actor: str | None = Depends(resolve_actor),
) -> InventoryScanResponse:
    """Scan-station move: add (in) or consume (out) a scanned item. The location is
    resolved from stock when omitted — a lone location is used automatically; when
    several qualify the response is ``needs_location`` with candidates to pick from
    (scan feedback)."""
    try:
        resolved = resolve_scan(
            db,
            barcode=payload.barcode,
            item_id=payload.item_id,
            direction=payload.direction,
            location_id=payload.location_id,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    # Ambiguous — hand the choice back rather than guessing.
    if resolved["location_id"] is None:
        item = resolved["item"]
        return InventoryScanResponse(
            status="needs_location",
            direction=payload.direction,
            item_id=item.id,
            item_name=item.name,
            candidate_locations=[ScanLocationOption(**c) for c in resolved["candidates"]],
        )

    item = resolved["item"]
    location_id = resolved["location_id"]

    # Consuming a container-tracked item is about a container, not an amount
    # (ADR 0023 D2). Scanning the empty tub reads the item's barcode, so the
    # gesture cannot say WHICH tub — resolve it here rather than letting the
    # "1 each = one full package" conversion take a container's worth out of
    # whatever FEFO picks.
    if payload.direction == "out":
        if payload.lot_id is not None:
            # A client-supplied lot is an ANSWER to this scan's prompt, so it has
            # to belong to what was actually scanned. Without this check a stale
            # prompt — or a crafted kiosk request — could consume an unrelated
            # item's container while the response cheerfully reports the scanned
            # item and its untouched balance.
            identity = lot_identity(db, payload.lot_id)
            if identity is None:
                raise HTTPException(status_code=404, detail="Lot not found")
            if identity != (item.id, location_id):
                raise HTTPException(
                    status_code=400,
                    detail=f"That container doesn't belong to {item.name} here — re-scan and pick again",
                )
        plan = (
            {"status": "targeted", "lot_id": payload.lot_id}
            if payload.lot_id is not None
            else plan_container_scan_out(db, item_id=item.id, location_id=location_id)
        )
        if plan["status"] == "needs_container":
            return InventoryScanResponse(
                status="needs_container",
                direction=payload.direction,
                item_id=item.id,
                item_name=item.name,
                resolved_location_id=location_id,
                resolved_location_name=(loc.name if (loc := get_location(db, location_id)) else None),
                candidate_containers=[
                    ScanContainerOption(
                        lot_id=lot.id,
                        quantity_remaining=lot.quantity_remaining,
                        unit=lot.unit,
                        expiration_date=lot.expiration_date,
                        status=lot.status,
                    )
                    for lot in plan["candidates"]
                ],
            )
        if plan["status"] == "targeted":
            try:
                tx, _qty = consume_container(db, lot_id=plan["lot_id"], actor=actor)
            except LookupError as exc:
                raise HTTPException(status_code=404, detail=str(exc)) from exc
            location = get_location(db, location_id)
            return InventoryScanResponse(
                status="done",
                direction="out",
                item_id=item.id,
                item_name=item.name,
                resolved_location_id=location_id,
                resolved_location_name=location.name if location else None,
                inventory=[
                    InventoryLineRead.model_validate(
                        balance_line(db, item_id=item.id, location_id=location_id, item_name=item.name)
                    )
                ],
                transaction_id=tx.id if tx is not None else None,
            )

    try:
        tx, row, item, direction = scan_move(
            db,
            barcode=payload.barcode,
            item_id=payload.item_id,
            location_id=location_id,
            direction=payload.direction,
            quantity=payload.quantity,
            unit=payload.unit,
            expiration_date=payload.expiration_date,
            actor=actor,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    location = get_location(db, row.location_id)
    return InventoryScanResponse(
        status="done",
        direction=direction,
        item_id=item.id,
        item_name=item.name,
        resolved_location_id=row.location_id,
        resolved_location_name=location.name if location else None,
        inventory=[
            InventoryLineRead.model_validate(
                {
                    "inventory_id": row.id,
                    "item_id": row.item_id,
                    "item_name": item.name,
                    "location_id": row.location_id,
                    "quantity": row.quantity,
                    "unit": row.unit,
                    "expiration_date": row.expiration_date,
                    "updated_at": row.updated_at,
                }
            )
        ],
        # The handle a one-tap undo needs (ADR 0023 D6) — scan_move has always
        # created a transaction, it just wasn't handed back.
        transaction_id=tx.id,
    )


@router.get("/transactions", response_model=TransactionListResponse)
def list_transactions_route(
    location_id: UUID | None = None,
    limit: int = Query(default=50, ge=1, le=500),
    db: Session = Depends(get_db),
) -> TransactionListResponse:
    rows = list_transactions(db, location_id=location_id, limit=limit)
    return TransactionListResponse(transactions=[TransactionRead.model_validate(row) for row in rows])


@router.post("/transactions/{transaction_id}/reverse", response_model=TransactionReverseResponse)
def reverse_transaction_route(
    transaction_id: UUID, db: Session = Depends(get_db)
) -> TransactionReverseResponse:
    try:
        result = reverse_transaction(db, transaction_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    return TransactionReverseResponse(**result)


@router.get("/events", response_model=InventoryEventListResponse)
def list_events_route(
    location_id: UUID | None = None,
    limit: int = Query(default=50, ge=1, le=500),
    since_hours: int | None = Query(
        default=None, ge=1, le=168, description="Only events from the last N hours."
    ),
    event_types: str | None = Query(
        default=None,
        description="Comma-separated event types to include, e.g. 'consumed,spoiled'.",
    ),
    db: Session = Depends(get_db),
) -> InventoryEventListResponse:
    """Authoritative inventory history from the append-only event log (ADR 0002)."""
    kinds = tuple(t.strip() for t in event_types.split(",") if t.strip()) if event_types else None
    rows = get_events(
        db, location_id=location_id, limit=limit, since_hours=since_hours, event_types=kinds
    )
    return InventoryEventListResponse(events=[InventoryEventRead.model_validate(row) for row in rows])


# --- Containers (GH #9): named sub-divisions of a location ----------------------


def _serialize_lots(db: Session, rows: list) -> list[LotRead]:
    """Enrich lot projection rows with the item + container names and the
    days-until-expiry the UI needs (the projection stores ids only)."""
    from app.models import Container, Item

    item_ids = {r.item_id for r in rows}
    names = (
        {row.id: row.name for row in db.execute(select(Item.id, Item.name).where(Item.id.in_(item_ids)))}
        if item_ids
        else {}
    )
    container_ids = {r.container_id for r in rows if r.container_id}
    containers = (
        {row.id: row.name for row in db.execute(select(Container.id, Container.name).where(Container.id.in_(container_ids)))}
        if container_ids
        else {}
    )
    today = date.today()
    out: list[LotRead] = []
    for r in rows:
        out.append(
            LotRead(
                lot_id=r.id,
                item_id=r.item_id,
                item_name=names.get(r.item_id, ""),
                location_id=r.location_id,
                container_id=r.container_id,
                container_name=containers.get(r.container_id) if r.container_id else None,
                status=r.status,
                quantity_remaining=r.quantity_remaining,
                unit=r.unit,
                container_size=r.container_size,
                container_unit=r.container_unit,
                sealed_count=r.sealed_count,
                expiration_date=r.expiration_date,
                unit_price=r.unit_price,
                frozen=r.frozen,
                days_until_expiration=(r.expiration_date - today).days if r.expiration_date else None,
            )
        )
    return out


def _lots_response(db: Session, item_id: UUID, location_id: UUID) -> LotListResponse:
    rows = list_lots(db, item_id=item_id, location_id=location_id)
    return LotListResponse(lots=_serialize_lots(db, rows))


def _lot_action_response(
    db: Session, item_id: UUID, location_id: UUID, tx: InventoryTransaction | None, qty: Decimal
) -> LotActionResponse:
    """The lot list after a targeted action, plus the handle needed to undo it.
    A ``None`` transaction means the container was already empty — a no-op, not
    a failure (ADR 0023 D13)."""
    rows = list_lots(db, item_id=item_id, location_id=location_id)
    return LotActionResponse(
        lots=_serialize_lots(db, rows),
        transaction_id=tx.id if tx is not None else None,
        quantity=qty,
        already_resolved=tx is None,
    )


def _require_lot(db: Session, lot_id: UUID) -> tuple[UUID, UUID]:
    """The lot's (item, location), captured BEFORE any refold — the projection
    rebuild replaces the row object wholesale.

    Falls back to the event log for a lot that has been fully drained, since the
    projection prunes empty lots: acting on one is an already-resolved no-op,
    not a missing resource."""
    identity = lot_identity(db, lot_id)
    if identity is None:
        raise HTTPException(status_code=404, detail="Lot not found")
    return identity


@router.get("/worklist", response_model=WorklistResponse)
def worklist_route(
    location_id: UUID,
    within_days: int = Query(default=7, ge=1, le=90),
    limit: int = Query(default=24, ge=1, le=100),
    db: Session = Depends(get_db),
) -> WorklistResponse:
    """What to act on at this location, ranked (ADR 0023 D8) — expiring stock
    first, then open containers, then what the household consumes often."""
    if get_location(db, location_id) is None:
        raise HTTPException(status_code=404, detail="Location not found")
    rows = build_worklist(db, location_id=location_id, within_days=within_days, limit=limit)
    return WorklistResponse(location_id=location_id, rows=[WorklistRow(**row) for row in rows])


@router.post("/lots/{lot_id}/finish", response_model=LotActionResponse)
def lot_finish_route(
    lot_id: UUID, db: Session = Depends(get_db), actor: str | None = Depends(resolve_actor)
) -> LotActionResponse:
    """"I finished this container" (ADR 0023 D1) — consume the whole of this open
    container, or one sealed unit of this batch, as USAGE rather than waste.

    Targeted on purpose: which container someone just emptied is a fact only they
    have, so it is named rather than guessed."""
    item_id, location_id = _require_lot(db, lot_id)
    try:
        tx, qty = consume_container(db, lot_id=lot_id, actor=actor)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return _lot_action_response(db, item_id, location_id, tx, qty)


@router.post("/lots/{lot_id}/consume", response_model=LotActionResponse)
def lot_consume_route(
    lot_id: UUID,
    payload: LotConsumeRequest,
    db: Session = Depends(get_db),
    actor: str | None = Depends(resolve_actor),
) -> LotActionResponse:
    """Partial draw from ONE named container ("used some of this tub")."""
    item_id, location_id = _require_lot(db, lot_id)
    try:
        tx, qty = consume_container(db, lot_id=lot_id, quantity=payload.quantity, actor=actor, note=payload.note)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return _lot_action_response(db, item_id, location_id, tx, qty)


@router.post("/set-expiration", response_model=SetExpirationResponse)
def set_expiration_route(
    payload: SetExpirationRequest, db: Session = Depends(get_db), actor: str | None = Depends(resolve_actor)
) -> SetExpirationResponse:
    """Set the best-by date for an item's on-hand stock at a location (ADR 0014).
    Relabels the item's live lot(s) so the on-hand line's expiry updates."""
    try:
        relabeled = set_item_expiration(
            db,
            item_id=payload.item_id,
            location_id=payload.location_id,
            expiration_date=payload.expiration_date,
            actor=actor,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return SetExpirationResponse(relabeled=relabeled)


@router.post("/lots/{lot_id}/open", response_model=LotListResponse)
def lot_open_route(
    lot_id: UUID, db: Session = Depends(get_db), actor: str | None = Depends(resolve_actor)
) -> LotListResponse:
    """Open one sealed container of a batch lot (ADR 0014)."""
    try:
        lot = open_container(db, lot_id=lot_id, actor=actor)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return _lots_response(db, lot.item_id, lot.location_id)


@router.post("/lots/{lot_id}/freeze", response_model=LotListResponse)
def lot_freeze_route(
    lot_id: UUID, db: Session = Depends(get_db), actor: str | None = Depends(resolve_actor)
) -> LotListResponse:
    try:
        lot = freeze_lot(db, lot_id=lot_id, actor=actor)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return _lots_response(db, lot.item_id, lot.location_id)


@router.post("/lots/{lot_id}/thaw", response_model=LotListResponse)
def lot_thaw_route(
    lot_id: UUID, db: Session = Depends(get_db), actor: str | None = Depends(resolve_actor)
) -> LotListResponse:
    try:
        lot = thaw_lot(db, lot_id=lot_id, actor=actor)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return _lots_response(db, lot.item_id, lot.location_id)


@router.post("/lots/{lot_id}/spoil", response_model=LotActionResponse)
def lot_spoil_route(
    lot_id: UUID, db: Session = Depends(get_db), actor: str | None = Depends(resolve_actor)
) -> LotActionResponse:
    """Discard/spoil this specific lot/container (ADR 0014)."""
    item_id, location_id = _require_lot(db, lot_id)
    try:
        tx, qty = discard_container(db, lot_id=lot_id, actor=actor)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _lot_action_response(db, item_id, location_id, tx, qty)


@router.post("/lots/{lot_id}/remove", response_model=LotListResponse)
def lot_remove_route(
    lot_id: UUID, db: Session = Depends(get_db), actor: str | None = Depends(resolve_actor)
) -> LotListResponse:
    """Remove a lot as a CORRECTION (added by mistake) — drops it off the balance
    without recording consumption or waste. Distinct from consuming or spoiling."""
    from app.models import InventoryLot

    lot = db.get(InventoryLot, lot_id)
    if lot is None:
        raise HTTPException(status_code=404, detail="Lot not found")
    item_id, location_id = lot.item_id, lot.location_id
    try:
        remove_lot_correction(db, lot_id=lot_id, actor=actor)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _lots_response(db, item_id, location_id)


@router.post("/lots/{lot_id}/relabel", response_model=LotListResponse)
def lot_relabel_route(
    lot_id: UUID,
    payload: LotRelabelRequest,
    db: Session = Depends(get_db),
    actor: str | None = Depends(resolve_actor),
) -> LotListResponse:
    """Set THIS lot's best-by date without touching other lots of the item."""
    try:
        lot = relabel_lot(db, lot_id=lot_id, expiration_date=payload.expiration_date, actor=actor)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return _lots_response(db, lot.item_id, lot.location_id)


@router.get("/lots", response_model=LotListResponse)
def list_lots_route(
    item_id: UUID | None = None,
    location_id: UUID | None = None,
    include_depleted: bool = False,
    db: Session = Depends(get_db),
) -> LotListResponse:
    """Lot/container breakdown for an item (and optionally location) — ADR 0014."""
    rows = list_lots(db, item_id=item_id, location_id=location_id, include_depleted=include_depleted)
    return LotListResponse(lots=_serialize_lots(db, rows))


@router.post("/open", response_model=LotListResponse)
def open_container_route(
    payload: OpenContainerRequest, db: Session = Depends(get_db), actor: str | None = Depends(resolve_actor)
) -> LotListResponse:
    """Open one sealed container of a batch lot (ADR 0014)."""
    try:
        lot = open_container(db, lot_id=payload.lot_id, actor=actor)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return _lots_response(db, lot.item_id, lot.location_id)


@router.post("/discard", response_model=LotActionResponse)
def discard_container_route(
    payload: DiscardContainerRequest, db: Session = Depends(get_db), actor: str | None = Depends(resolve_actor)
) -> LotActionResponse:
    """Discard/spoil a specific container without touching the others (ADR 0014)."""
    item_id, location_id = _require_lot(db, payload.lot_id)
    try:
        tx, qty = discard_container(
            db, lot_id=payload.lot_id, quantity=payload.quantity, actor=actor, reason=payload.reason, note=payload.note
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _lot_action_response(db, item_id, location_id, tx, qty)


@router.get("/containers", response_model=ContainerListResponse)
def list_containers_route(
    location_id: UUID | None = None, db: Session = Depends(get_db)
) -> ContainerListResponse:
    rows = container_service.list_containers(db, location_id=location_id)
    return ContainerListResponse(containers=[ContainerRead.model_validate(r) for r in rows])


@router.post("/containers", response_model=ContainerRead, status_code=201)
def create_container_route(payload: ContainerCreate, db: Session = Depends(get_db)) -> ContainerRead:
    try:
        row = container_service.create_container(
            db, location_id=payload.location_id, name=payload.name, description=payload.description
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return ContainerRead.model_validate(row)


@router.delete("/containers/{container_id}", status_code=204)
def delete_container_route(container_id: UUID, db: Session = Depends(get_db)) -> None:
    try:
        container_service.delete_container(db, container_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
