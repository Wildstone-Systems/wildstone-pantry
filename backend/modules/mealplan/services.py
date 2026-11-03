"""Meal plan service: the week calendar (epic #28, #29).

Rules this enforces:
- An entry names its meal in one way: a recipe, an item, or free text.
- Recipe/item entries show the live name (they follow edits). ``title`` keeps
  the name as of the last write, so a deleted recipe/item still reads sensibly.
- Servings are pinned on the entry (default: the recipe's servings, else 1).
- ``planned ⇄ skipped``; ``cooked`` arrives with cook-from-plan (#32).
- Nothing reserves stock, and nothing auto-skips or rolls forward.
"""

from __future__ import annotations

import logging
from datetime import date, timedelta
from uuid import UUID

from sqlalchemy.orm import Session

from app.services.profiles import get_profile
from modules.catalog.services.items import get_item
from modules.mealplan import repository
from modules.mealplan.models import MealPlanEntry
from modules.mealplan.schemas import MealPlanEntryCreate, MealPlanEntryUpdate
from modules.planning.services.recipes import get_recipe

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


def create(db: Session, payload: MealPlanEntryCreate, *, actor: str | None, source: str = "manual") -> dict:
    meal = _resolve_meal(db, recipe_id=payload.recipe_id, item_id=payload.item_id, title=payload.title)
    _check_profile(db, payload.profile_id)
    recipe = meal.pop("_recipe", None)
    servings = payload.servings or (recipe.servings if recipe is not None and recipe.servings else 1)
    entry = repository.add(
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
    db.commit()
    db.refresh(entry)
    logger.info("meal plan entry %s added for %s %s (%s)", entry.id, entry.plan_date, entry.meal_type, source)
    return to_read(db, entry)


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
        entry.position = changes["position"]
    elif moved:
        # Flush first so the new cell's max ignores this entry's old spot.
        db.flush()
        entry.position = repository.next_position(db, plan_date=entry.plan_date, meal_type=entry.meal_type)

    db.commit()
    db.refresh(entry)
    return to_read(db, entry)


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
