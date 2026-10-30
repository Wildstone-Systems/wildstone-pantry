"""First-run setup state + starter seeding (ADR 0018 §7).

The service layer for the setup wizard: routes stay thin (route → service per
AGENTS.md), and this module is the single owner of the wizard's persistence —
the completion check and the preset seeding that the platform previously did
inline in the route.
"""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Category, InventoryLocation, Item
from app.services.app_settings import KEY_SETUP_COMPLETED, is_true

PRESETS: dict[str, dict[str, list[str]]] = {
    "minimal": {
        "locations": ["Pantry", "Fridge", "Freezer"],
        "categories": [],
    },
    "typical": {
        "locations": ["Pantry", "Fridge", "Freezer", "Garage"],
        "categories": ["produce", "dairy", "frozen", "dry_goods"],
    },
}


def setup_completed(db: Session) -> bool:
    if is_true(db, KEY_SETUP_COMPLETED):
        return True
    # Pre-wizard installs: real inventory data means this household is already
    # set up. (Items, not locations — the wizard's own seed step creates
    # locations mid-flow and must not self-complete the wizard.)
    item_count = db.execute(select(func.count()).select_from(Item)).scalar_one()
    return item_count > 0


def seed_preset(db: Session, preset_name: str) -> dict:
    """Idempotently create the preset's starter locations/categories."""
    preset = PRESETS[preset_name]
    created = {"locations": 0, "categories": 0}
    for name in preset["locations"]:
        exists = db.execute(select(InventoryLocation).where(InventoryLocation.name == name)).scalar_one_or_none()
        if not exists:
            db.add(InventoryLocation(name=name))
            created["locations"] += 1
    for name in preset["categories"]:
        exists = db.execute(select(Category).where(Category.name == name)).scalar_one_or_none()
        if not exists:
            db.add(Category(name=name))
            created["categories"] += 1
    db.commit()
    return created
