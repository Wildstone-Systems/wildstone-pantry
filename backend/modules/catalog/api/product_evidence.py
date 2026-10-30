"""Explicit provisioned-admin product facts; no agent or kiosk save authority."""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.security import deny_agent_keys, require_authenticated_admin
from app.db import get_db
from modules.catalog.schemas.product_evidence import ProductEvidenceRead, SaveProductEvidence
from modules.catalog.services import product_evidence as service

router = APIRouter(tags=["product evidence"], dependencies=[Depends(deny_agent_keys)])


def call(fn, *args):
    try:
        return fn(*args)
    except LookupError:
        raise HTTPException(404, "Active product unavailable") from None
    except ValueError:
        raise HTTPException(409, "Product source or saved facts changed; reload before saving") from None


@router.get("/items/{item_id}/structured-evidence", response_model=ProductEvidenceRead)
def read(item_id: UUID, db: Session = Depends(get_db), actor=Depends(require_authenticated_admin)):
    return call(service.read, db, item_id)


@router.put("/items/{item_id}/structured-evidence", response_model=ProductEvidenceRead)
def save(
    item_id: UUID,
    command: SaveProductEvidence,
    db: Session = Depends(get_db),
    actor=Depends(require_authenticated_admin),
):
    return call(service.save, db, item_id, command, actor.id)
