from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.security import allow_kiosk
from app.db import get_db
from modules.shopping.connectors import NotConfiguredError
from modules.shopping.services.offers import search_offers
from modules.shopping.schemas.shopping_list import (
    ExportResultResponse,
    ExportTargetRead,
    ExportTargetsResponse,
    InstacartPushResult,
    KrogerCartPushResult,
    OfferSelectRequest,
    ShoppingListAddRequest,
    ShoppingListEntryRead,
    ShoppingListResponse,
    ShoppingListUpdateRequest,
)
from modules.shopping.services import shopping_list as service

# Everyday kiosk surface (open in kiosk mode via allow_kiosk).
router = APIRouter(prefix="/shopping-list", tags=["shopping-list"], dependencies=[Depends(allow_kiosk)])


class OfferRead(BaseModel):
    provider: str
    name: str
    vendor: str | None = None
    vendor_sku: str | None = None
    upc: str | None = None
    brand: str | None = None
    size: str | None = None
    price: float | None = None
    image_url: str | None = None


class OfferSearchResponse(BaseModel):
    query: str
    offers: list[OfferRead]
    throttled: list[str]  # provider keys that were rate-limited (retry later)


@router.get("/search", response_model=OfferSearchResponse)
def search_route(
    q: str = Query(..., min_length=1, description="What to shop for, e.g. 'spaghetti sauce'"),
    limit: int = Query(default=10, ge=1, le=25),
    provider: str | None = Query(default=None, description="Restrict to one source, e.g. 'kroger'"),
    db: Session = Depends(get_db),
) -> OfferSearchResponse:
    """Search the integrations for purchasable offers matching a need (ADR 0013).

    Food results need a USDA key; retailer (priced) results come from Kroger.
    ``provider=kroger`` restricts to Kroger."""
    return OfferSearchResponse.model_validate(search_offers(q, limit=limit, db=db, provider=provider))


@router.post("/from-offer", response_model=ShoppingListEntryRead, status_code=201)
def from_offer_route(payload: OfferSelectRequest, db: Session = Depends(get_db)) -> ShoppingListEntryRead:
    """Resolve a searched offer to a catalog item and put it on the list (ADR 0013)."""
    offer = payload.model_dump(exclude={"quantity", "unit", "entry_id", "item_id"})
    if offer.get("price") is not None:
        offer["price"] = float(offer["price"])
    try:
        entry = service.select_offer(
            db,
            offer=offer,
            quantity=payload.quantity,
            unit=payload.unit,
            entry_id=payload.entry_id,
            item_id=payload.item_id,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return ShoppingListEntryRead.model_validate(entry)


@router.get("", response_model=ShoppingListResponse)
def list_route(db: Session = Depends(get_db)) -> ShoppingListResponse:
    rows = service.list_entries(db)
    return ShoppingListResponse(items=[ShoppingListEntryRead.model_validate(r) for r in rows])


@router.post("", response_model=ShoppingListEntryRead, status_code=201)
def add_route(payload: ShoppingListAddRequest, db: Session = Depends(get_db)) -> ShoppingListEntryRead:
    row = service.add_manual_entry(
        db,
        name=payload.name,
        item_id=payload.item_id,
        quantity=payload.quantity,
        unit=payload.unit,
        note=payload.note,
    )
    return ShoppingListEntryRead.model_validate(row)


@router.patch("/{entry_id}", response_model=ShoppingListEntryRead)
def update_route(
    entry_id: UUID, payload: ShoppingListUpdateRequest, db: Session = Depends(get_db)
) -> ShoppingListEntryRead:
    fields = payload.model_fields_set
    try:
        row = service.update_entry(
            db,
            entry_id=entry_id,
            quantity=payload.quantity,
            status=payload.status,
            note=payload.note,
            note_set="note" in fields,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return ShoppingListEntryRead.model_validate(row)


@router.delete("/{entry_id}", status_code=204)
def delete_route(entry_id: UUID, db: Session = Depends(get_db)) -> None:
    try:
        service.delete_entry(db, entry_id=entry_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/kroger-cart", response_model=KrogerCartPushResult)
def kroger_cart_route(db: Session = Depends(get_db)) -> KrogerCartPushResult:
    """Add the pending buy list to the connected user's Kroger cart (Phase 4).

    Requires a Kroger account linked under Integrations. Entries without a Kroger
    UPC are skipped and reported."""
    from app.services.entitlement import FEATURE_STORE_INTEGRATIONS, check_brokered_feature
    from app.services.integration_broker import BrokerEntitlementLapsed, BrokerError
    from modules.shopping.services.kroger_cart import KrogerCartError, KrogerNotConnected

    try:
        result = service.push_to_kroger_cart(db)
    except KrogerNotConnected as exc:
        # Not connected locally: if a broker is configured, the brokered path is
        # the premium feature (ADR 0018 §3) — 402 beats 409 for the upsell.
        check_brokered_feature(FEATURE_STORE_INTEGRATIONS)
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except BrokerEntitlementLapsed as exc:
        raise HTTPException(
            status_code=402,
            detail={"error": "feature_not_available", "feature": FEATURE_STORE_INTEGRATIONS, "message": str(exc)},
        ) from exc
    except BrokerError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except KrogerCartError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return KrogerCartPushResult(**result)


@router.post("/instacart", response_model=InstacartPushResult)
def instacart_route(db: Session = Depends(get_db)) -> InstacartPushResult:
    """Turn the pending buy list into an Instacart shoppable-list link (ADR 0013
    Phase 4b — multi-retailer ordering). Dormant until an Instacart API key is set;
    409 means not configured."""
    from app.services.entitlement import FEATURE_STORE_INTEGRATIONS, check_brokered_feature
    from modules.shopping.connectors import NotConfiguredError
    from modules.shopping.connectors.instacart import InstacartError

    try:
        result = service.create_instacart_page(db)
    except NotConfiguredError as exc:
        # Same brokered-branch rule as the Kroger push (ADR 0018 §3).
        check_brokered_feature(FEATURE_STORE_INTEGRATIONS)
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except InstacartError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return InstacartPushResult(**result)


@router.get("/export/targets", response_model=ExportTargetsResponse)
def export_targets_route() -> ExportTargetsResponse:
    """Available export targets + whether each is configured (Kroger is dormant)."""
    return ExportTargetsResponse(targets=[ExportTargetRead(**t) for t in service.available_targets()])


@router.get("/export", response_model=ExportResultResponse)
def export_route(target: str = "text", db: Session = Depends(get_db)) -> ExportResultResponse:
    """Export the pending buy list for a target (text/csv now; vendor carts later)."""
    try:
        result = service.export_shopping_list(db, target_key=target)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except NotConfiguredError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except NotImplementedError as exc:
        raise HTTPException(status_code=501, detail=str(exc)) from exc
    return ExportResultResponse(
        target=result.target,
        format=result.format,
        content_type=result.content_type,
        body=result.body,
        filename=result.filename,
    )
