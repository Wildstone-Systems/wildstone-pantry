"""Contract ``pantry-1`` — the surface Wildstone's adapter calls (ADR 0026 §2).

Authenticated like any agent: an ``X-Agent-Key``. Queries are GET so a
read-only key can answer them; actions are POST and need the ``write`` scope
(agent-key scopes are enforced by HTTP method, ADR 0010).
"""

from fastapi import APIRouter, Body, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.core.security import require_user
from app.db import get_db
from modules.wildstone.services import contract

router = APIRouter(
    prefix="/integrations/wildstone/v1",
    tags=["wildstone"],
    dependencies=[Depends(require_user)],
)


@router.get("/capabilities")
def get_capabilities() -> dict:
    return contract.capabilities()


@router.get("/query/{capability}")
def run_query(capability: str, request: Request, db: Session = Depends(get_db)) -> dict:
    """Answer one query. Parameters are the query string, e.g. ``?list=groceries``."""
    try:
        return contract.query(db, capability, dict(request.query_params))
    except contract.UnknownCapability as exc:
        raise HTTPException(status_code=404, detail=f"PantryOS has no query {capability!r}") from exc
    except contract.BadRequest as exc:
        raise HTTPException(status_code=422, detail=f"{capability}: {exc}") from exc


@router.post("/act/{capability}")
def run_action(capability: str, body: dict = Body(default_factory=dict), db: Session = Depends(get_db)) -> dict:
    try:
        return contract.act(db, capability, body)
    except contract.UnknownCapability as exc:
        raise HTTPException(status_code=404, detail=f"PantryOS has no action {capability!r}") from exc
    except contract.BadRequest as exc:
        raise HTTPException(status_code=422, detail=f"{capability}: {exc}") from exc
