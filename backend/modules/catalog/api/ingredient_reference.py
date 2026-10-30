from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.security import require_user
from app.db import get_db
from modules.catalog.schemas.ingredient_reference import (
    IngredientReferenceCreate,
    IngredientReferenceListResponse,
    IngredientReferenceRead,
    IngredientReferenceUpdate,
)
from modules.catalog.services.ingredient_reference import (
    create_reference,
    delete_reference,
    get_reference,
    list_reference,
    update_reference,
)

router = APIRouter(
    prefix="/ingredient-reference",
    tags=["ingredient-reference"],
    dependencies=[Depends(require_user)],
)


@router.get("", response_model=IngredientReferenceListResponse)
def list_reference_route(db: Session = Depends(get_db)) -> IngredientReferenceListResponse:
    rows = list_reference(db)
    return IngredientReferenceListResponse(
        references=[IngredientReferenceRead.model_validate(row) for row in rows]
    )


@router.post("", response_model=IngredientReferenceRead, status_code=status.HTTP_201_CREATED)
def create_reference_route(
    payload: IngredientReferenceCreate, db: Session = Depends(get_db)
) -> IngredientReferenceRead:
    try:
        row = create_reference(db, **payload.model_dump())
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="A reference with that name already exists") from exc
    return IngredientReferenceRead.model_validate(row)


@router.put("/{reference_id}", response_model=IngredientReferenceRead)
def update_reference_route(
    reference_id: UUID, payload: IngredientReferenceUpdate, db: Session = Depends(get_db)
) -> IngredientReferenceRead:
    row = get_reference(db, reference_id)
    if not row:
        raise HTTPException(status_code=404, detail="Reference not found")
    try:
        updated = update_reference(db, row, **payload.model_dump(exclude_unset=True))
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="A reference with that name already exists") from exc
    return IngredientReferenceRead.model_validate(updated)


@router.delete("/{reference_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_reference_route(reference_id: UUID, db: Session = Depends(get_db)) -> None:
    row = get_reference(db, reference_id)
    if not row:
        raise HTTPException(status_code=404, detail="Reference not found")
    delete_reference(db, row)
