from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.security import require_user
from app.db import get_db
from modules.planning.schemas.substitution import (
    SubstitutionCreate,
    SubstitutionListResponse,
    SubstitutionRead,
    SubstitutionUpdate,
)
from modules.planning.services.substitutions import (
    delete_substitution,
    get_substitution,
    list_substitutions_for,
    upsert_substitution,
)

router = APIRouter(prefix="/substitutions", tags=["substitutions"], dependencies=[Depends(require_user)])


@router.get("/for/{item_id}", response_model=SubstitutionListResponse)
def list_for_item_route(item_id: UUID, db: Session = Depends(get_db)) -> SubstitutionListResponse:
    rows = list_substitutions_for(db, item_id)
    return SubstitutionListResponse(substitutions=[SubstitutionRead.model_validate(r) for r in rows])


@router.post("", response_model=SubstitutionRead, status_code=status.HTTP_201_CREATED)
def create_substitution_route(payload: SubstitutionCreate, db: Session = Depends(get_db)) -> SubstitutionRead:
    try:
        row = upsert_substitution(
            db,
            from_item_id=payload.from_item_id,
            to_item_id=payload.to_item_id,
            ratio=payload.ratio,
            unit=payload.unit,
            outcome=payload.outcome,
            notes=payload.notes,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return SubstitutionRead.model_validate(row)


@router.put("/{substitution_id}", response_model=SubstitutionRead)
def update_substitution_route(
    substitution_id: UUID, payload: SubstitutionUpdate, db: Session = Depends(get_db)
) -> SubstitutionRead:
    row = get_substitution(db, substitution_id)
    if not row:
        raise HTTPException(status_code=404, detail="Substitution not found")
    try:
        updated = upsert_substitution(
            db,
            from_item_id=row.from_item_id,
            to_item_id=row.to_item_id,
            ratio=payload.ratio,
            unit=payload.unit,
            outcome=payload.outcome,
            notes=payload.notes,
        )
    except (LookupError, ValueError) as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return SubstitutionRead.model_validate(updated)


@router.delete("/{substitution_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_substitution_route(substitution_id: UUID, db: Session = Depends(get_db)) -> None:
    row = get_substitution(db, substitution_id)
    if not row:
        raise HTTPException(status_code=404, detail="Substitution not found")
    delete_substitution(db, row)
