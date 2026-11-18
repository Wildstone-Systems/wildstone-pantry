from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.security import require_user
from app.db import get_db
from modules.agent.schemas.resolve_items import ResolveItemsRequest, ResolveItemsResponse
from modules.agent.services.resolve_items import MATCH_CREATED, MATCH_NONE, resolve_items

router = APIRouter(prefix="/agent", tags=["agent"], dependencies=[Depends(require_user)])


@router.post("/resolve-items", response_model=ResolveItemsResponse)
def resolve_items_route(payload: ResolveItemsRequest, db: Session = Depends(get_db)) -> ResolveItemsResponse:
    """Map product names/barcodes (e.g. receipt lines) to catalog item ids.

    Dry run unless ``create_missing`` is true. Does not change stock — follow
    up with POST /api/inventory/add per line."""
    rows = resolve_items(db, lines=[line.model_dump() for line in payload.lines], create_missing=payload.create_missing)
    return ResolveItemsResponse(
        items=rows,
        created_count=sum(1 for r in rows if r["match"] == MATCH_CREATED),
        not_found_count=sum(1 for r in rows if r["match"] == MATCH_NONE),
    )
