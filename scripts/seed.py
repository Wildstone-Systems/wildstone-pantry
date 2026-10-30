#!/usr/bin/env python3
from __future__ import annotations

from sqlalchemy import select

from app.db import SessionLocal
from app.models import Category, InventoryLocation


DEFAULT_CATEGORIES = ["produce", "dairy", "frozen", "dry_goods"]
DEFAULT_LOCATIONS = ["Pantry", "Fridge", "Freezer", "Garage"]


def seed() -> None:
    with SessionLocal() as db:
        for name in DEFAULT_CATEGORIES:
            existing = db.execute(select(Category).where(Category.name == name)).scalar_one_or_none()
            if not existing:
                db.add(Category(name=name))

        for name in DEFAULT_LOCATIONS:
            existing = db.execute(select(InventoryLocation).where(InventoryLocation.name == name)).scalar_one_or_none()
            if not existing:
                db.add(InventoryLocation(name=name))

        db.commit()


if __name__ == "__main__":
    seed()
    print("Seed complete")
