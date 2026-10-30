"""Retailer capabilities + search-handoff sessions (ADR 0019 D1/D3/D5).

Everyday kiosk surface, like the shopping list itself: the handoff loop is a
phone-in-the-store flow, so it must work for any household member without an
admin login.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.core.security import allow_kiosk
from app.db import get_db
from modules.shopping.retailers import list_adapters
from modules.shopping.schemas.handoff import (
    HandoffCompleteRequest,
    HandoffItemRead,
    HandoffItemUpdateRequest,
    HandoffSessionRead,
    HandoffSessionsResponse,
    HandoffStartRequest,
    RetailerRead,
    RetailersResponse,
)
from modules.shopping.services import handoff as service

retailers_router = APIRouter(prefix="/retailers", tags=["retailers"], dependencies=[Depends(allow_kiosk)])
router = APIRouter(prefix="/shopping-list/handoff", tags=["shopping-list"], dependencies=[Depends(allow_kiosk)])


@retailers_router.get("", response_model=RetailersResponse)
def list_retailers_route(db: Session = Depends(get_db)) -> RetailersResponse:
    """Every known retailer with declared vs currently-usable capabilities.
    Search-link retailers are pure data rows; API retailers overlay more."""
    out = []
    for adapter in list_adapters(db):
        out.append(
            RetailerRead(
                key=adapter.key,
                label=adapter.label,
                capabilities=sorted(adapter.capabilities()),
                available=sorted(adapter.available(db)),
                status=adapter._row.status,
            )
        )
    return RetailersResponse(retailers=out)


@router.get("", response_model=HandoffSessionsResponse)
def list_sessions_route(
    status: str | None = Query(default="active"), db: Session = Depends(get_db)
) -> HandoffSessionsResponse:
    """Recent handoff sessions (default: resumable active ones)."""
    rows = service.list_sessions(db, status=status or None)
    return HandoffSessionsResponse(sessions=[HandoffSessionRead.model_validate(r) for r in rows])


@router.post("", response_model=HandoffSessionRead, status_code=201)
def start_route(payload: HandoffStartRequest, db: Session = Depends(get_db)) -> HandoffSessionRead:
    """Snapshot the pending buy list into a guided shopping session."""
    try:
        session = service.start_session(db, retailer_key=payload.retailer)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return HandoffSessionRead.model_validate(session)


@router.get("/{session_id}", response_model=HandoffSessionRead)
def get_route(session_id: UUID, db: Session = Depends(get_db)) -> HandoffSessionRead:
    try:
        session = service.get_session(db, session_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return HandoffSessionRead.model_validate(session)


@router.patch("/{session_id}/items/{item_id}", response_model=HandoffItemRead)
def update_item_route(
    session_id: UUID, item_id: UUID, payload: HandoffItemUpdateRequest, db: Session = Depends(get_db)
) -> HandoffItemRead:
    """Mark progress as the user works the retailer's site (opened/added/
    substituted/unavailable/skipped). All transitions are user-driven."""
    fields = payload.model_fields_set
    try:
        item = service.update_item(
            db,
            session_id=session_id,
            handoff_item_id=item_id,
            state=payload.state,
            external_id=payload.external_id if "external_id" in fields else None,
            external_name=payload.external_name if "external_name" in fields else None,
            external_url=payload.external_url if "external_url" in fields else None,
            note=payload.note if "note" in fields else None,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return HandoffItemRead.model_validate(item)


@router.post("/{session_id}/complete", response_model=HandoffSessionRead)
def complete_route(
    session_id: UUID, payload: HandoffCompleteRequest, db: Session = Depends(get_db)
) -> HandoffSessionRead:
    """The post-shop confirmation (ADR 0019 D5): purchased items close their
    list entries, teach the product map, and become an idempotent Purchase."""
    try:
        session = service.complete_session(
            db,
            session_id=session_id,
            purchased_item_ids=payload.purchased_item_ids,
            location_id=payload.location_id,
            item_locations=payload.item_locations,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return HandoffSessionRead.model_validate(session)
