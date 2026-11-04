"""Nutrition forecast for the meal plan (#34): calories planned per person per
day, against each profile's ``daily_calorie_target``. Read-only.

How a planned meal counts:
- recipe: its per-serving calories (planning's ``recipe_nutrition``) × the
  entry's pinned servings; an ingredient without nutrition data makes the
  number ``partial`` rather than silently low;
- item: servings × one "each" of the item (its ``calories_per_unit``, as meal
  logging uses, else catalog ``macros_for_amount``);
- free text ("Takeout") or no data at all: **unknown**, never zero.

Who it counts for: an entry for one person is all theirs; a household entry
is shared evenly by the active household profiles. Skipped meals don't count.
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy.orm import Session

from app.services.profiles import list_profiles
from modules.catalog.services.ingredient_reference import macros_for_amount
from modules.catalog.services.items import get_item
from modules.mealplan.services import MAX_DAYS, MealPlanInputError, list_week
from modules.planning.services.recipes import recipe_nutrition


def _entry_calories(db: Session, entry: dict, cache: dict) -> tuple[Decimal | None, bool]:
    """``(calories for the whole entry, partial)``; calories None = unknown."""
    if entry["kind"] == "recipe" and entry["recipe_id"]:
        key = ("recipe", entry["recipe_id"])
        if key not in cache:
            try:
                cache[key] = recipe_nutrition(db, entry["recipe_id"])
            except LookupError:
                cache[key] = None
        info = cache[key]
        per_serving = info["per_serving"]["calories"] if info else None
        if per_serving is None:
            return None, False
        return Decimal(str(per_serving)) * entry["servings"], not info["complete"]
    if entry["kind"] == "item" and entry["item_id"]:
        key = ("item", entry["item_id"])
        if key not in cache:
            item = get_item(db, entry["item_id"])
            if item is None:
                cache[key] = None
            elif item.calories_per_unit is not None:
                # The same per-"each" number meal logging uses.
                cache[key] = item.calories_per_unit
            else:
                macros = macros_for_amount(db, item, Decimal(1), "each")
                cache[key] = macros.get("calories") if macros else None
        each = cache[key]
        if each is None:
            return None, False
        return Decimal(str(each)) * entry["servings"], False
    return None, False


def forecast(db: Session, *, start: date, days: int = 7) -> dict:
    if not 1 <= days <= MAX_DAYS:
        raise MealPlanInputError(f"days must be between 1 and {MAX_DAYS}")
    people = [p for p in list_profiles(db) if p.is_active]
    dates = [start + timedelta(days=i) for i in range(days)]
    # per person → per day → running totals
    totals = {
        str(p.id): {d: {"calories": Decimal("0"), "partial": False, "unknown_meals": 0, "meals": 0} for d in dates}
        for p in people
    }

    cache: dict = {}
    for entry in list_week(db, start=start, days=days)["entries"]:
        if entry["status"] == "skipped":
            continue
        calories, partial = _entry_calories(db, entry, cache)
        if entry["profile_id"] is not None:
            eaters = [str(entry["profile_id"])] if str(entry["profile_id"]) in totals else []
        else:
            eaters = list(totals)
        if not eaters:
            continue
        share = calories / len(eaters) if calories is not None else None
        for person in eaters:
            day = totals[person][entry["plan_date"]]
            day["meals"] += 1
            if share is None:
                day["unknown_meals"] += 1
            else:
                day["calories"] += share
                day["partial"] = day["partial"] or partial

    return {
        "start": start,
        "days": days,
        "people": [
            {
                "profile_id": p.id,
                "display_name": p.display_name,
                "target": float(p.daily_calorie_target) if p.daily_calorie_target is not None else None,
                "days": [
                    {
                        "date": d,
                        "calories": round(float(totals[str(p.id)][d]["calories"])),
                        "partial": totals[str(p.id)][d]["partial"],
                        "unknown_meals": totals[str(p.id)][d]["unknown_meals"],
                        "meals": totals[str(p.id)][d]["meals"],
                    }
                    for d in dates
                ],
            }
            for p in people
        ],
    }
