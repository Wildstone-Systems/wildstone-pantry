"""Meal plan service: the week calendar (epic #28, #29).

Rules this enforces:
- An entry names its meal in one way: a recipe, an item, or free text.
- Recipe/item entries show the live name (they follow edits). ``title`` keeps
  the name as of the last write, so a deleted recipe/item still reads sensibly.
- Servings are pinned on the entry (default: the recipe's servings, else 1).
- ``planned ⇄ skipped``; ``cooked`` only by linking a real cook (#32).
- Nothing reserves stock, and nothing auto-skips or rolls forward.
"""

from __future__ import annotations

import hashlib
import logging
from datetime import date, timedelta
from uuid import UUID

from sqlalchemy.orm import Session

from app.services.profiles import get_profile
from modules.catalog.services.items import get_item
from modules.mealplan import repository
from modules.mealplan.models import MealPlanEntry
from modules.mealplan.schemas import MealPlanEntryCreate, MealPlanEntryUpdate
from modules.planning.services.recipes import get_cook_event, get_recipe

logger = logging.getLogger(__name__)

MAX_DAYS = 42
_MEAL_FIELDS = ("recipe_id", "item_id", "title")


class MealPlanInputError(ValueError):
    """The request is well-formed but names something that can't be planned."""


class MealPlanStateError(ValueError):
    """The entry's current status doesn't allow that change."""


def week_start(day: date) -> date:
    """The Sunday on or before ``day`` (weeks start on Sunday, #28)."""
    return day - timedelta(days=(day.weekday() + 1) % 7)


# --- reading -------------------------------------------------------------------


def _live_name(db: Session, entry: MealPlanEntry, cache: dict) -> tuple[str, str]:
    """``(kind, name)`` for display. Falls back to ``title`` when the recipe or
    item is gone (SET NULL on Postgres; a dangling id on SQLite)."""
    if entry.recipe_id is not None:
        key = ("recipe", entry.recipe_id)
        if key not in cache:
            cache[key] = get_recipe(db, entry.recipe_id)
        if cache[key] is not None:
            return "recipe", cache[key].name
        return "recipe", entry.title or "Deleted recipe"
    if entry.item_id is not None:
        key = ("item", entry.item_id)
        if key not in cache:
            cache[key] = get_item(db, entry.item_id)
        if cache[key] is not None:
            return "item", cache[key].name
        return "item", entry.title or "Deleted item"
    return "text", entry.title or ""


def to_read(db: Session, entry: MealPlanEntry, cache: dict | None = None) -> dict:
    kind, name = _live_name(db, entry, {} if cache is None else cache)
    return {
        "id": entry.id,
        "plan_date": entry.plan_date,
        "meal_type": entry.meal_type,
        "position": entry.position,
        "kind": kind,
        "meal": name,
        "recipe_id": entry.recipe_id,
        "item_id": entry.item_id,
        "title": entry.title,
        "servings": entry.servings,
        "profile_id": entry.profile_id,
        "status": entry.status,
        "cook_event_id": entry.cook_event_id,
        "source": entry.source,
        "note": entry.note,
        "created_by": entry.created_by,
        "created_at": entry.created_at,
        "updated_at": entry.updated_at,
    }


def list_week(db: Session, *, start: date, days: int = 7) -> dict:
    if not 1 <= days <= MAX_DAYS:
        raise MealPlanInputError(f"days must be between 1 and {MAX_DAYS}")
    cache: dict = {}
    entries = repository.list_entries(db, start=start, end=start + timedelta(days=days))
    return {"start": start, "days": days, "entries": [to_read(db, e, cache) for e in entries]}


def get(db: Session, entry_id: UUID) -> dict:
    return to_read(db, _require(db, entry_id))


# --- writing -------------------------------------------------------------------


def _require(db: Session, entry_id: UUID) -> MealPlanEntry:
    entry = repository.get_entry(db, entry_id)
    if entry is None:
        raise LookupError("Meal plan entry not found")
    return entry


def _clean_title(title: str | None) -> str | None:
    if title is None:
        return None
    title = title.strip()
    return title or None


def _resolve_meal(db: Session, *, recipe_id: UUID | None, item_id: UUID | None, title: str | None) -> dict:
    """Validate the one thing that names the meal; return the columns to store."""
    title = _clean_title(title)
    named = [f for f, v in (("recipe_id", recipe_id), ("item_id", item_id), ("title", title)) if v is not None]
    if len(named) != 1:
        raise MealPlanInputError("name the meal with exactly one of recipe_id, item_id or title")
    if recipe_id is not None:
        recipe = get_recipe(db, recipe_id)
        if recipe is None:
            raise MealPlanInputError("no recipe with that recipe_id")
        return {"recipe_id": recipe.id, "item_id": None, "title": recipe.name, "_recipe": recipe}
    if item_id is not None:
        item = get_item(db, item_id)
        if item is None:
            raise MealPlanInputError("no item with that item_id")
        return {"recipe_id": None, "item_id": item.id, "title": item.name}
    return {"recipe_id": None, "item_id": None, "title": title}


def _check_profile(db: Session, profile_id: UUID | None) -> None:
    if profile_id is None:
        return
    profile = get_profile(db, profile_id)
    if profile is None or not profile.is_active:
        raise MealPlanInputError("no active household profile has that profile_id")


def _build(db: Session, payload: MealPlanEntryCreate, *, actor: str | None, source: str) -> MealPlanEntry:
    """Validate one new entry and add it to the session (not committed)."""
    meal = _resolve_meal(db, recipe_id=payload.recipe_id, item_id=payload.item_id, title=payload.title)
    _check_profile(db, payload.profile_id)
    recipe = meal.pop("_recipe", None)
    servings = payload.servings or (recipe.servings if recipe is not None and recipe.servings else 1)
    # next_position autoflushes, so entries built earlier in the same batch count.
    return repository.add(
        db,
        MealPlanEntry(
            plan_date=payload.plan_date,
            meal_type=payload.meal_type,
            position=repository.next_position(db, plan_date=payload.plan_date, meal_type=payload.meal_type),
            servings=servings,
            profile_id=payload.profile_id,
            note=payload.note,
            status="planned",
            source=source,
            created_by=actor,
            **meal,
        ),
    )


def create(db: Session, payload: MealPlanEntryCreate, *, actor: str | None, source: str = "manual") -> dict:
    entry = _build(db, payload, actor=actor, source=source)
    db.commit()
    db.refresh(entry)
    logger.info("meal plan entry %s added for %s %s (%s)", entry.id, entry.plan_date, entry.meal_type, source)
    return to_read(db, entry)


def create_many(
    db: Session, payloads: list[MealPlanEntryCreate], *, actor: str | None, source: str = "manual"
) -> list[dict]:
    """Add several entries as one unit: all of them, or none if any is invalid
    (an agent drafting a week should never leave half a week behind)."""
    try:
        entries = [_build(db, payload, actor=actor, source=source) for payload in payloads]
    except Exception:
        db.rollback()
        raise
    db.commit()
    for entry in entries:
        db.refresh(entry)
    logger.info("meal plan: %d entries added together (%s)", len(entries), source)
    cache: dict = {}
    return [to_read(db, e, cache) for e in entries]


def update(db: Session, entry_id: UUID, payload: MealPlanEntryUpdate) -> dict:
    entry = _require(db, entry_id)
    changes = payload.model_dump(exclude_unset=True)

    meal_changes = {f: changes.pop(f) for f in _MEAL_FIELDS if f in changes}
    if meal_changes:
        meal = _resolve_meal(
            db,
            recipe_id=meal_changes.get("recipe_id"),
            item_id=meal_changes.get("item_id"),
            title=meal_changes.get("title"),
        )
        meal.pop("_recipe", None)
        same_meal = (meal["recipe_id"], meal["item_id"]) == (entry.recipe_id, entry.item_id) and (
            meal["recipe_id"] or meal["item_id"] or meal["title"] == entry.title
        )
        if entry.status == "cooked" and not same_meal:
            # The cook was of the old meal: it no longer fulfils this one.
            entry.status = "planned"
            entry.cook_event_id = None
        for column, value in meal.items():
            setattr(entry, column, value)

    for required in ("plan_date", "meal_type", "servings", "position"):
        if required in changes and changes[required] is None:
            raise MealPlanInputError(f"{required} cannot be cleared")

    if "profile_id" in changes:
        _check_profile(db, changes["profile_id"])
        entry.profile_id = changes["profile_id"]
    if "servings" in changes:
        entry.servings = changes["servings"]
    if "note" in changes:
        entry.note = changes["note"]

    moved = ("plan_date" in changes and changes["plan_date"] != entry.plan_date) or (
        "meal_type" in changes and changes["meal_type"] != entry.meal_type
    )
    if "plan_date" in changes:
        entry.plan_date = changes["plan_date"]
    if "meal_type" in changes:
        entry.meal_type = changes["meal_type"]
    if "position" in changes:
        db.flush()
        _place(db, entry, changes["position"])
    elif moved:
        # Flush first so the new cell's max ignores this entry's old spot.
        db.flush()
        entry.position = repository.next_position(db, plan_date=entry.plan_date, meal_type=entry.meal_type)

    db.commit()
    db.refresh(entry)
    return to_read(db, entry)


def _place(db: Session, entry: MealPlanEntry, position: int) -> None:
    """Put ``entry`` at ``position`` in its cell and renumber the cell 0..n-1,
    so a reorder never leaves two entries tied on the same position."""
    others = [e for e in repository.cell_entries(db, plan_date=entry.plan_date, meal_type=entry.meal_type) if e.id != entry.id]
    others.insert(min(position, len(others)), entry)
    for index, e in enumerate(others):
        e.position = index


def remove(db: Session, entry_id: UUID) -> None:
    repository.delete(db, _require(db, entry_id))
    db.commit()


def skip(db: Session, entry_id: UUID) -> dict:
    entry = _require(db, entry_id)
    if entry.status != "planned":
        raise MealPlanStateError(f"only a planned meal can be skipped (this one is {entry.status})")
    entry.status = "skipped"
    db.commit()
    db.refresh(entry)
    return to_read(db, entry)


def reset(db: Session, entry_id: UUID) -> dict:
    """Back to ``planned``. Clears the cook link but never undoes a cook."""
    entry = _require(db, entry_id)
    entry.status = "planned"
    entry.cook_event_id = None
    db.commit()
    db.refresh(entry)
    return to_read(db, entry)


# --- cook from plan (#32) -------------------------------------------------------


def mark_cooked(db: Session, entry_id: UUID, *, cook_event_id: UUID) -> dict:
    """Link a finished cook to a planned recipe meal and mark it ``cooked``.

    Cooking itself always goes through the cook-along; this only records which
    cook fulfilled the plan. The cook must be of the entry's recipe. Linking
    the same cook again is a no-op, so a retried request is safe.
    """
    entry = _require(db, entry_id)
    if entry.recipe_id is None:
        raise MealPlanStateError("only a recipe meal can be cooked from the plan")
    cook = get_cook_event(db, cook_event_id)
    if cook is None:
        raise MealPlanInputError("no cook with that cook_event_id")
    if cook["recipe_id"] != entry.recipe_id:
        raise MealPlanInputError("that cook was of a different recipe")
    if entry.status == "cooked":
        if entry.cook_event_id == cook_event_id:
            return to_read(db, entry)
        raise MealPlanStateError("this meal is already marked cooked by another cook")
    entry.status = "cooked"
    entry.cook_event_id = cook_event_id
    db.commit()
    db.refresh(entry)
    logger.info("meal plan entry %s cooked (cook %s)", entry.id, cook_event_id)
    return to_read(db, entry)


def cook_candidates(db: Session, *, recipe_id: UUID, on: date) -> list[dict]:
    """Planned, uncooked meals of ``recipe_id`` on ``on``: what to offer in
    "Was this Sunday's dinner?" after a cook that didn't start from the plan."""
    entries = repository.list_entries(db, start=on, end=on + timedelta(days=1))
    cache: dict = {}
    return [to_read(db, e, cache) for e in entries if e.recipe_id == recipe_id and e.status == "planned"]


# --- change detection (#33) -----------------------------------------------------


def plan_state(db: Session, *, today: date) -> dict:
    """What the meal plan looks like right now, for noticing a change
    (Wildstone ``pantry.meal_plan_changed``), like the shopping list's
    ``list_state``.

    ``fingerprint`` changes whenever an entry is added, edited, moved, cooked,
    skipped or removed; ``changed_at`` is the newest edit (None once the plan
    is empty: a deletion leaves no timestamp); ``upcoming`` counts planned
    meals from ``today`` on.
    """
    digest = hashlib.sha256()
    latest = None
    upcoming = 0
    for row in repository.fingerprint_rows(db):
        entry_id, plan_date, meal_type, position, status, updated_at = row
        digest.update(f"{entry_id}|{plan_date}|{meal_type}|{position}|{status}|{updated_at}\n".encode())
        if latest is None or updated_at > latest:
            latest = updated_at
        if status == "planned" and plan_date >= today:
            upcoming += 1
    return {"fingerprint": digest.hexdigest()[:32], "upcoming": upcoming, "changed_at": latest}
