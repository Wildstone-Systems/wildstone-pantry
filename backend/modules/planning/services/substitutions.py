"""Ingredient substitutions — learned mappings feeding canonical matching (ADR 0011
D5 / ADR 0015 §4).

Substitutions are taught at cook time: when an ingredient is out of stock the user
picks a substitute, which is upserted here (``upsert_substitution``) so it's factored
into recipe availability/suggestions (via ``canonical_subs_by_from``, unit-aware per
ADR 0017 §7). CRUD is also exposed for direct editing.
"""

from __future__ import annotations

from decimal import Decimal
from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.models import IngredientSubstitution, Item
from modules.catalog.services.match_evidence import item_identity
from modules.catalog.services.units import to_recipe_unit


def basis_matches(row, original_unit):
    return row.from_unit is None or (original_unit is not None and to_recipe_unit(original_unit) == to_recipe_unit(row.from_unit))


def find_directional(db, source, target, *, lock=False):
    predicates = [
        (IngredientSubstitution.from_item_id == UUID(str(source["item_id"]))) &
        (IngredientSubstitution.to_item_id == UUID(str(target["item_id"])))
    ]
    if source.get("canonical_ingredient") and target.get("canonical_ingredient"):
        predicates.append((IngredientSubstitution.from_canonical == source["canonical_ingredient"]) &
                          (IngredientSubstitution.to_canonical == target["canonical_ingredient"]))
    query = select(IngredientSubstitution).where(or_(*predicates))
    rows = db.execute(query.with_for_update() if lock else query).scalars().all()
    if len(rows) > 1:
        raise ValueError("Conflicting existing global mappings require explicit catalog cleanup")
    return rows[0] if rows else None


def global_snapshot(row):
    return None if row is None else {"id": str(row.id), "ratio": str(row.ratio), "unit": row.unit,
        "from_unit": row.from_unit, "approved": row.approved}


def write_scoped_global(db, source, target, *, ratio, unit, from_unit, replace):
    """Human-authorized global write inside its caller's atomic transaction."""
    row = find_directional(db, source, target, lock=True)
    if row and (row.ratio != ratio or row.unit != unit or row.from_unit != from_unit or not row.approved) and not replace:
        raise ValueError("This permanent choice replaces an existing mapping, including its original-unit basis; explicitly acknowledge old and new values")
    if row is None:
        row = IngredientSubstitution(from_item_id=UUID(source["item_id"]), to_item_id=UUID(target["item_id"]),
            from_canonical=source.get("canonical_ingredient"), to_canonical=target.get("canonical_ingredient"),
            from_label=source["name"], to_label=target["name"], outcome="acceptable")
    row.ratio, row.unit, row.from_unit, row.approved = ratio, unit, from_unit, True
    db.add(row)
    db.flush()
    return row


def cook_basis(db, from_item_id, to_item_id, original_unit):
    source, target = item_identity(db, from_item_id), item_identity(db, to_item_id)
    row = find_directional(db, source, target)
    if row and row.from_unit is not None and not basis_matches(row, original_unit):
        raise ValueError("Saved substitution applies per " + row.from_unit + "; explicitly review a ratio for this original unit")
    return row if row and row.from_unit is not None else None


def list_substitutions_for(db: Session, item_id: UUID) -> list[IngredientSubstitution]:
    return list(
        db.execute(
            select(IngredientSubstitution).where(IngredientSubstitution.from_item_id == item_id)
        ).scalars().all()
    )


def get_substitution(db: Session, substitution_id: UUID) -> IngredientSubstitution | None:
    return db.execute(
        select(IngredientSubstitution).where(IngredientSubstitution.id == substitution_id)
    ).scalar_one_or_none()


def _find(db: Session, from_item_id: UUID, to_item_id: UUID) -> IngredientSubstitution | None:
    return db.execute(
        select(IngredientSubstitution).where(
            IngredientSubstitution.from_item_id == from_item_id,
            IngredientSubstitution.to_item_id == to_item_id,
        )
    ).scalar_one_or_none()


def saved_ratio(db: Session, from_item_id: UUID, to_item_id: UUID, *, original_unit: str | None = None) -> Decimal | None:
    """The ratio already learned for from→to, or None — a read, never a write."""
    row = _find(db, from_item_id, to_item_id)
    if row is not None and not basis_matches(row, original_unit):
        raise ValueError("Saved ratio has a different original-unit basis; review this cook")
    return Decimal(row.ratio) if row is not None else None


def upsert_substitution(
    db: Session,
    *,
    from_item_id: UUID,
    to_item_id: UUID,
    ratio: Decimal | None = None,
    unit: str | None = None,
    outcome: str | None = None,
    notes: str | None = None,
    original_unit: str | None = None,
) -> IngredientSubstitution:
    """Create or update a from→to mapping. Used both by the CRUD API and by the
    cook path when a swap is first chosen (learning). Only provided fields update
    an existing row; a new row defaults ratio=1.0, unit=each, outcome=acceptable."""
    if from_item_id == to_item_id:
        raise ValueError("An item cannot substitute for itself")
    from_item = db.execute(select(Item).where(Item.id == from_item_id)).scalar_one_or_none()
    to_item = db.execute(select(Item).where(Item.id == to_item_id)).scalar_one_or_none()
    for item_id, item in ((from_item_id, from_item), (to_item_id, to_item)):
        if item is None:
            raise LookupError(f"Item not found: {item_id}")

    row = _find(db, from_item_id, to_item_id)
    if row is None:
        candidate = find_directional(db,
            {"item_id": str(from_item_id), "canonical_ingredient": from_item.canonical_ingredient},
            {"item_id": str(to_item_id), "canonical_ingredient": to_item.canonical_ingredient})
        if candidate is not None and candidate.from_unit is not None:
            row = candidate
    if row is not None and row.from_unit is not None:
        if original_unit is not None and not basis_matches(row, original_unit):
            raise ValueError("Saved substitution has a different original-unit basis")
        if (ratio is not None and Decimal(str(ratio)) != row.ratio) or (unit is not None and unit != row.unit):
            raise ValueError("Changing this basis-bound permanent mapping requires explicit replacement review")
    if row is None:
        row = IngredientSubstitution(
            from_item_id=from_item_id,
            to_item_id=to_item_id,
            # Canonical keys + labels so a learned sub feeds identity-generalised
            # matching (ADR 0015 Phase 3). Learned = approved.
            from_canonical=from_item.canonical_ingredient,
            to_canonical=to_item.canonical_ingredient,
            from_label=from_item.name,
            to_label=to_item.name,
            approved=True,
            ratio=Decimal(str(ratio)) if ratio is not None else Decimal("1"),
            unit=unit or "each",
            outcome=outcome or "acceptable",
            notes=notes,
        )
        db.add(row)
    else:
        # Keep canonical keys current with the items (they may have been aliased).
        row.from_canonical = from_item.canonical_ingredient
        row.to_canonical = to_item.canonical_ingredient
        row.from_label = row.from_label or from_item.name
        row.to_label = row.to_label or to_item.name
        row.approved = True
        if ratio is not None:
            row.ratio = Decimal(str(ratio))
        if unit is not None:
            row.unit = unit
        if outcome is not None:
            row.outcome = outcome
        if notes is not None:
            row.notes = notes
    db.commit()
    db.refresh(row)
    return row


def delete_substitution(db: Session, row: IngredientSubstitution) -> None:
    db.delete(row)
    db.commit()


def canonical_subs_by_from(db: Session) -> dict[str, list[IngredientSubstitution]]:
    """All canonical-keyed substitutions grouped by ``from_canonical`` — the lookup
    availability/suggestions use to answer "what can stand in for this ingredient?"
    (ADR 0015 §4). Rows without a from key (pure item-provenance legacy) are skipped."""
    rows = db.execute(
        select(IngredientSubstitution).where(IngredientSubstitution.from_canonical.is_not(None))
    ).scalars().all()
    grouped: dict[str, list[IngredientSubstitution]] = {}
    for row in rows:
        grouped.setdefault(row.from_canonical, []).append(row)
    return grouped


def approve_substitution(db: Session, substitution_id: UUID) -> IngredientSubstitution:
    """Confirm a suggested substitution (ADR 0015 §5): mark it approved so it counts
    toward availability and is offered automatically from now on."""
    row = get_substitution(db, substitution_id)
    if row is None:
        raise LookupError("Substitution not found")
    row.approved = True
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


