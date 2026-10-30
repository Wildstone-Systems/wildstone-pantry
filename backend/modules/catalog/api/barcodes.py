from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.security import allow_kiosk
from app.db import get_db
from app.schemas import (
    BarcodeAliasCreate,
    BarcodeAliasRead,
    BarcodeLookupResponse,
    BarcodeScanRequest,
    BarcodeScanResponse,
)
from app.services import lookup_barcode, register_alias
from modules.catalog.services.identity import IdentityConflict
from modules.catalog.services.shelf_life import learned_shelf_life

router = APIRouter(prefix="/barcodes", tags=["barcodes"], dependencies=[Depends(allow_kiosk)])


@router.get("/lookup/{barcode}", response_model=BarcodeLookupResponse)
def barcode_lookup_route(barcode: str, db: Session = Depends(get_db)) -> BarcodeLookupResponse:
    try:
        return BarcodeLookupResponse.model_validate(lookup_barcode(db, barcode, auto_create=False))
    except IdentityConflict as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/scan", response_model=BarcodeScanResponse)
def barcode_scan_route(payload: BarcodeScanRequest, db: Session = Depends(get_db)) -> BarcodeScanResponse:
    """Scan a barcode: look up locally, then externally, optionally auto-create Item."""
    try:
        result = lookup_barcode(db, payload.barcode, auto_create=payload.auto_create)
    except IdentityConflict as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    created = False
    if result.get("source", "").startswith("external:") and payload.auto_create and result.get("item_id"):
        created = True

    location_candidates = []
    shelf_life_hint = None
    item_id = result.get("item_id")
    if item_id:
        hint = learned_shelf_life(db, item_id)
        shelf_life_hint = hint.as_dict() if hint else None
        # Lazy import: mirrors resolve_scan's own cross-module import (inventory
        # already imports catalog's resolve_local_item the same way) — avoids a
        # module-load-time cycle between the two.
        from modules.inventory.services.inventory import scan_location_candidates

        location_candidates = scan_location_candidates(db, item_id, "in")

    return BarcodeScanResponse(
        barcode=result["barcode"],
        found=result["found"],
        created=created,
        item_id=item_id,
        item_name=result.get("item_name"),
        source=result.get("source"),
        item=result.get("item"),
        product=result.get("product"),
        location_candidates=location_candidates,
        shelf_life_hint=shelf_life_hint,
    )


@router.post("/alias", response_model=BarcodeAliasRead)
def register_alias_route(payload: BarcodeAliasCreate, db: Session = Depends(get_db)) -> BarcodeAliasRead:
    try:
        row = register_alias(db, payload.barcode, payload.item_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="Alias conflict") from exc

    return BarcodeAliasRead(
        id=row.id,
        barcode=row.barcode,
        item_id=row.item_id,
        created_at=row.created_at,
    )
