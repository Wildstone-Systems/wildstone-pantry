from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.security import allow_kiosk
from app.db import get_db
from app.schemas import LocationCreate, LocationListResponse, LocationRead, LocationUpdate
from app.services import (
    create_location,
    delete_location,
    get_location,
    list_locations,
    location_in_use,
    update_location,
)

router = APIRouter(prefix="/locations", tags=["locations"], dependencies=[Depends(allow_kiosk)])


@router.get("", response_model=LocationListResponse)
def list_locations_route(
    include_inactive: bool = False, db: Session = Depends(get_db)
) -> LocationListResponse:
    rows = list_locations(db, include_inactive=include_inactive)
    return LocationListResponse(locations=[LocationRead.model_validate(row) for row in rows])


@router.post("", response_model=LocationRead, status_code=status.HTTP_201_CREATED)
def create_location_route(payload: LocationCreate, db: Session = Depends(get_db)) -> LocationRead:
    try:
        row = create_location(db, payload.name, payload.description)
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="Location already exists") from exc

    return LocationRead.model_validate(row)


@router.get("/{location_id}", response_model=LocationRead)
def get_location_route(location_id: UUID, db: Session = Depends(get_db)) -> LocationRead:
    location = get_location(db, location_id)
    if not location:
        raise HTTPException(status_code=404, detail="Location not found")

    return LocationRead.model_validate(location)


@router.put("/{location_id}", response_model=LocationRead)
def update_location_route(
    location_id: UUID, payload: LocationUpdate, db: Session = Depends(get_db)
) -> LocationRead:
    location = get_location(db, location_id)
    if not location:
        raise HTTPException(status_code=404, detail="Location not found")

    try:
        updated = update_location(db, location, payload.model_dump(exclude_unset=True))
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="Location already exists") from exc

    return LocationRead.model_validate(updated)


@router.delete("/{location_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_location_route(location_id: UUID, db: Session = Depends(get_db)) -> None:
    location = get_location(db, location_id)
    if not location:
        raise HTTPException(status_code=404, detail="Location not found")

    if location_in_use(db, location_id):
        raise HTTPException(
            status_code=409,
            detail=(
                "Location has inventory history and cannot be deleted without losing it. "
                "Archive the location instead (PUT is_active=false) to keep its records."
            ),
        )

    delete_location(db, location)
