"""Containers — named sub-divisions of a storage location (GH #9).

A container belongs to exactly one location; stock can be placed in one at
intake. Placement is recorded on the (immutable) event and folded onto the lot
projection, so it survives a from-scratch rebuild.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Container, InventoryLocation


def list_containers(db: Session, *, location_id: UUID | None = None) -> list[Container]:
    stmt = select(Container).order_by(Container.name)
    if location_id is not None:
        stmt = stmt.where(Container.location_id == location_id)
    return list(db.execute(stmt).scalars())


def get_container(db: Session, container_id: UUID) -> Container | None:
    return db.get(Container, container_id)


def create_container(db: Session, *, location_id: UUID, name: str, description: str | None) -> Container:
    if db.get(InventoryLocation, location_id) is None:
        raise LookupError("Location not found")
    row = Container(location_id=location_id, name=name, description=description)
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def delete_container(db: Session, container_id: UUID) -> None:
    row = get_container(db, container_id)
    if row is None:
        raise LookupError("Container not found")
    db.delete(row)
    db.commit()


def resolve_container(db: Session, container_id: UUID | None, location_id: UUID) -> UUID | None:
    """Validate a container belongs to the intake location.

    ``None`` means "loose in the location" (no container). A container from a
    different location is rejected — placement can't cross locations.
    """
    if container_id is None:
        return None
    container = get_container(db, container_id)
    if container is None:
        raise LookupError("Container not found")
    if container.location_id != location_id:
        raise ValueError("Container belongs to a different location")
    return container_id
