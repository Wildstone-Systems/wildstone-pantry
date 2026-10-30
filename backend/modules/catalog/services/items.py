from __future__ import annotations

from uuid import UUID

from sqlalchemy import exists, or_, select
from sqlalchemy.orm import Session

from app.models import Inventory, Item
from modules.catalog.schemas import ItemCreate, ItemUpdate
from modules.catalog.services.barcodes import normalize_barcode
from modules.catalog.services.canonical import CANONICAL_VERSION, canonical_ingredient
from modules.catalog.services.identity import change_identity
from modules.catalog.services.units import base_unit_for, default_base_unit, infer_stock_mode


def _has_stock(db: Session, item_id: UUID) -> bool:
    """Whether anything of this item is on hand anywhere."""
    return db.query(
        exists().where(Inventory.item_id == item_id, Inventory.quantity > 0)
    ).scalar()


def _reject_impossible_nutrition(data: dict) -> None:
    """Refuse arithmetically impossible macros before they are stored.

    Deliberately at the service layer, not the schema: every write path — the
    API, barcode import, receipt import, recipe enrichment, the agent — goes
    through create_item/update_item, and a per-field Pydantic validator cannot
    see the cross-field arithmetic (protein + fat + carbs > 100 g) that catches
    most of these.

    Stored impossibilities do not stay local. "Minced Garlic" carrying 400 g of
    carbohydrate per 100 g is summed into daily calories, scaled by every recipe
    that uses it, and charted — and by the time a total looks wrong nobody can
    say which row caused it. Only IMPOSSIBLE values are refused; merely
    inconsistent ones are stored and surfaced (see services.plausibility).
    """
    from modules.catalog.services.plausibility import blocking_issues

    issues = blocking_issues(data)
    if issues:
        raise ValueError(
            "These nutrition values can't be right: "
            + "; ".join(f"{issue.field} — {issue.message}" for issue in issues)
        )


def create_item(db: Session, payload: ItemCreate) -> Item:
    data = payload.model_dump()
    from modules.catalog.services.nutrition_authority import explicit_nutrition
    explicit_nutrition(data, payload.model_fields_set)
    _reject_impossible_nutrition(data)
    if data.get("barcode"):
        data["barcode"] = normalize_barcode(data["barcode"])
    if not data.get("stock_mode"):
        # Calling a product variable-weight IS saying it is measured by weight —
        # one UPC covering packages of different sizes only makes sense for
        # something weighed (ADR 0025 §1). Without this a size-less roast would
        # default to 'counted'/'each' and the first check-in would have nowhere
        # honest to put 4.87 lb.
        data["stock_mode"] = "measured" if data.get("variable_weight") else infer_stock_mode(data.get("size_unit"))
    # Base (stored/consumed) unit default (ADR 0016): counted → each, measured → the
    # size's dimension. stock_mode is resolved just above, so read it back from data.
    if not data.get("base_unit"):
        data["base_unit"] = default_base_unit(data.get("stock_mode"), data.get("size_unit"))
        if data.get("variable_weight") and data["base_unit"] == "each":
            data["base_unit"] = "g"  # a weighed thing with no size on file yet
    # Propose the canonical ingredient key (ADR 0015) so recipe availability can
    # match this item to name-divergent records for the same thing.
    if not data.get("canonical_ingredient"):
        data["canonical_ingredient"] = canonical_ingredient(data.get("name"), data.get("brand"))
    data["canonical_provenance"] = "generated"
    data["canonical_version"] = CANONICAL_VERSION

    item = Item(**data)
    db.add(item)
    db.commit()
    db.refresh(item)
    return item


def list_items(
    db: Session,
    *,
    include_inactive: bool = False,
    search: str | None = None,
    in_stock_only: bool = False,
    limit: int | None = None,
) -> list[Item]:
    """List catalog items, newest first, optionally filtered by a search term.

    ``search`` is a case-insensitive substring match over the fields you'd actually
    recognise an item by — name, brand, and its canonical ingredient key, so a search
    for "chicken" also finds "Tyson Boneless Thighs" once that item is keyed to it.
    Underscores and percent signs are escaped; they are LIKE wildcards, not intent.

    ``in_stock_only`` narrows to items with a positive balance somewhere (scan
    station "consume" typeahead — no point offering to take from an item you
    don't have).
    """
    stmt = select(Item).order_by(Item.created_at.desc())
    if not include_inactive:
        stmt = stmt.where(Item.is_active.is_(True))
    if in_stock_only:
        stmt = stmt.where(
            exists().where(Inventory.item_id == Item.id, Inventory.quantity > 0)
        )

    term = (search or "").strip()
    if term:
        escaped = term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        pattern = f"%{escaped}%"
        stmt = stmt.where(
            or_(
                Item.name.ilike(pattern, escape="\\"),
                Item.brand.ilike(pattern, escape="\\"),
                Item.canonical_ingredient.ilike(pattern, escape="\\"),
            )
        )
    if limit is not None:
        stmt = stmt.limit(max(1, min(limit, 50)))
    return list(db.execute(stmt).scalars().all())


def get_item(db: Session, item_id: UUID) -> Item | None:
    return db.execute(select(Item).where(Item.id == item_id)).scalar_one_or_none()


def update_item(db: Session, item: Item, payload: ItemUpdate) -> Item:
    data = payload.model_dump(exclude_unset=True)
    from modules.catalog.services.nutrition_authority import explicit_nutrition
    explicit_nutrition(data, payload.model_fields_set, item)
    explicit_identity = "canonical_ingredient" in data
    confirmed = data.pop("confirm_identity_change", False)
    expected_key = data.pop("expected_canonical_ingredient", None)
    expected_revision = data.pop("expected_canonical_revision", None)
    key = data.pop("canonical_ingredient", None)
    if explicit_identity:
        if not confirmed or "expected_canonical_ingredient" not in payload.model_fields_set or expected_revision is None:
            raise ValueError("Identity changes require confirmation and the reviewed key and revision")
        if key is not None:
            key = " ".join(key.split())
            if not key:
                raise ValueError("Identity must contain a meaningful key, or null for exact item only")
    # Merged with what is already stored: a partial update that sets only fat
    # can still push the macro sum past 100 g, and checking the patch alone
    # would miss it.
    _reject_impossible_nutrition(
        {
            column: data.get(column, getattr(item, column, None))
            for column in (
                "energy_kcal_100g", "protein_g_100g", "fat_g_100g", "carbs_g_100g",
                "fiber_g_100g", "sugars_g_100g", "saturated_fat_g_100g", "sodium_g_100g",
            )
        }
    )
    if "barcode" in data:
        data["barcode"] = normalize_barcode(data["barcode"]) if data["barcode"] else None
    for field, value in data.items():
        setattr(item, field, value)

    # Only generated keys track display edits. Confirmed and uncertain historical
    # aliases stay intact until a separately reviewed identity change.
    if explicit_identity:
        change_identity(db, item, key=key, version=None, provenance="confirmed",
                        expected_key=expected_key, expected_revision=expected_revision, check_key=True)
    elif ("name" in data or "brand" in data) and item.canonical_provenance == "generated":
        change_identity(db, item, key=canonical_ingredient(item.name, item.brand),
                        version=CANONICAL_VERSION, provenance="generated")

    # Re-derive the base unit when tracking/size changes (ADR 0016), unless it was
    # set explicitly. NOTE: any existing on-hand is still in the OLD base — callers
    # that change this must re-express the balance (inventory reexpress) to match.
    if ("stock_mode" in data or "size_unit" in data) and "base_unit" not in data:
        item.base_unit = default_base_unit(item.stock_mode, item.size_unit)

    # Turning on catch weight on an item nobody has stocked yet also settles how
    # it's measured (see create_item). An item that already HAS stock keeps its
    # base unit: re-expressing a live balance is an explicit migration, never a
    # side effect of ticking a checkbox (ADR 0016).
    if data.get("variable_weight") and "base_unit" not in data and "stock_mode" not in data:
        if base_unit_for(item.base_unit, item.stock_mode, item.size_unit) == "each" and not _has_stock(db, item.id):
            item.stock_mode = "measured"
            item.base_unit = default_base_unit("measured", item.size_unit)
            if item.base_unit == "each":
                item.base_unit = "g"

    db.add(item)
    db.commit()
    db.refresh(item)
    return item


def archive_item(db: Session, item: Item) -> Item:
    """Retire an item from the catalogue without destroying anything.

    This is what "delete" means for an item, and it is deliberately reversible.
    Every child table hanging off ``items`` — inventory events above all — is
    wired ``ON DELETE CASCADE``, so a real delete silently takes the item's
    entire ledger history with it. In an append-only event store (ADR 0002)
    that is not a delete, it is a rewrite of the past: consumption trends,
    calorie history and cook records that referenced the item quietly change
    shape, and nothing in the UI says so.

    Archiving hides the item from catalogue and picker lists while every
    reference to it keeps resolving. See ``purge_item`` for the rare case where
    the rows really should go.
    """
    item.is_active = False
    db.add(item)
    db.commit()
    db.refresh(item)
    return item


def restore_item(db: Session, item: Item) -> Item:
    """Put an archived item back in the catalogue."""
    item.is_active = True
    db.add(item)
    db.commit()
    db.refresh(item)
    return item


def item_usage(db: Session, item: Item) -> dict:
    """What a purge would destroy, so the caller can say so before doing it.

    Counted rather than summarised: "16 ledger events" is a number someone can
    weigh, where "has history" is not.
    """
    from app.models import RecipeIngredient
    from modules.inventory.models.event import InventoryEvent
    from modules.inventory.models.projections import InventoryLot

    lots = db.query(InventoryLot).filter_by(item_id=item.id).all()
    return {
        "item_id": item.id,
        "name": item.name,
        "is_active": item.is_active,
        "ledger_events": db.query(InventoryEvent).filter_by(item_id=item.id).count(),
        "lots": len(lots),
        "on_hand": float(sum(lot.quantity_remaining for lot in lots)),
        "recipe_ingredients": db.query(RecipeIngredient).filter_by(item_id=item.id).count(),
    }


def purge_item(db: Session, item: Item) -> dict:
    """Permanently delete an item and everything that cascades from it.

    Irreversible, and the cascade reaches the event log. Callers must confirm
    explicitly — see the route — because there is nothing to undo afterwards.
    Returns what was destroyed so it can be reported rather than guessed at.
    """
    from modules.inventory.models.event import InventoryEvent

    destroyed = item_usage(db, item)

    # Delete the ledger rows explicitly rather than trusting the FK's
    # ON DELETE CASCADE. The cascade only fires where the database enforces
    # foreign keys, and the ORM has no relationship to `inventory_events` to
    # fall back on — so on any engine that doesn't enforce them, a "purge" left
    # the events behind, pointing at an item that no longer exists. A function
    # that reports "destroyed 16 ledger events" has to have destroyed them.
    db.query(InventoryEvent).filter_by(item_id=item.id).delete(synchronize_session=False)

    db.delete(item)
    db.commit()
    return destroyed
