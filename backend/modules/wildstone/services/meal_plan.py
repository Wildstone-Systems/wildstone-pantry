"""Meal planning for agents (epic #28, #33): "what's for dinner this week?",
"put chilli on Thursday", "plan next week".

An agent speaks in dates, meal names and members; the meal plan wants ids.
This resolves them — and, like meal logging, when a recipe name is not certain
it asks (the candidates come back) rather than guessing. Entries an agent adds
land as ``planned`` with ``source=agent``; anyone can edit or remove them.
Nothing here buys anything.
"""

from __future__ import annotations

from datetime import date
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy.orm import Session

from modules.mealplan import services as mealplan
from modules.mealplan.schemas import MealPlanEntryCreate
from modules.planning.services.recipes import list_recipes
from modules.wildstone.services.meals import MealRequestError, profile_for, uuid_field
from modules.wildstone.services.members import member_for

MAX_CANDIDATES = 5
MAX_WEEK_MEALS = 60
MEAL_TYPES = ("breakfast", "lunch", "dinner", "snack")


def _date(value, field: str) -> date:
    try:
        return date.fromisoformat(str(value))
    except (TypeError, ValueError) as exc:
        raise MealRequestError(f"{field} must be a date (YYYY-MM-DD)") from exc


def _int(value, field: str, *, default: int, low: int, high: int) -> int:
    if value is None or value == "":
        return default
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise MealRequestError(f"{field} must be a whole number") from exc
    if not low <= number <= high:
        raise MealRequestError(f"{field} must be between {low} and {high}")
    return number


def _recipe_id(db: Session, body: dict) -> UUID | None:
    """The recipe the agent means — certain, or a MealRequestError naming the
    candidates (then sent back as recipe_id)."""
    if body.get("recipe_id"):
        return uuid_field(body["recipe_id"], "recipe_id")
    wanted = str(body.get("recipe") or "").strip()
    if not wanted:
        return None
    recipes = list_recipes(db)
    exact = [r for r in recipes if r.name.strip().lower() == wanted.lower()]
    if len(exact) == 1:
        return exact[0].id
    close = exact or [r for r in recipes if wanted.lower() in r.name.lower()]
    if len(close) == 1:
        return close[0].id
    if not close:
        raise MealRequestError(
            f"no recipe is called {wanted!r}; send it as a title for a meal without a recipe"
        )
    options = "; ".join(f"{r.name} (recipe_id {r.id})" for r in close[:MAX_CANDIDATES])
    raise MealRequestError(f"{wanted!r} could be more than one recipe: {options} — send the recipe_id")


def _entry(db: Session, body: dict, *, default_date: date | None = None) -> MealPlanEntryCreate:
    if not isinstance(body, dict):
        raise MealRequestError("each meal must be an object")
    raw_date = body.get("date") or (default_date.isoformat() if default_date else None)
    if not raw_date:
        raise MealRequestError("needs a date (YYYY-MM-DD)")
    meal_type = str(body.get("meal_type") or "dinner").strip().lower()
    if meal_type not in MEAL_TYPES:
        raise MealRequestError(f"meal_type must be one of {', '.join(MEAL_TYPES)}")
    recipe_id = _recipe_id(db, body)
    item_id = uuid_field(body["item_id"], "item_id") if body.get("item_id") and recipe_id is None else None
    title = None if (recipe_id or item_id) else (str(body.get("title") or "").strip() or None)
    if not (recipe_id or item_id or title):
        raise MealRequestError("needs a recipe, recipe_id, item_id or title")
    # A member is optional here: no member means the whole household.
    profile_id = UUID(profile_for(db, body)) if (body.get("member") or body.get("profile_id")) else None
    try:
        return MealPlanEntryCreate(
            plan_date=_date(raw_date, "date"),
            meal_type=meal_type,
            recipe_id=recipe_id,
            item_id=item_id,
            title=title,
            servings=None
            if body.get("servings") in (None, "")
            else _int(body["servings"], "servings", default=1, low=1, high=100),
            profile_id=profile_id,
            note=(str(body["note"]).strip()[:2000] or None) if body.get("note") else None,
        )
    except ValidationError as exc:
        raise MealRequestError(exc.errors()[0].get("msg", "invalid meal")) from exc


def _contract_entry(db: Session, row: dict) -> dict:
    return {
        "entry_id": str(row["id"]),
        "date": row["plan_date"].isoformat(),
        "meal_type": row["meal_type"],
        "meal": row["meal"],
        "kind": row["kind"],
        "recipe_id": str(row["recipe_id"]) if row["recipe_id"] else None,
        "servings": row["servings"],
        "status": row["status"],
        # The Wildstone member it is for, when it is for one person and that
        # person is linked; null = the whole household (or an unlinked profile).
        "for": member_for(db, str(row["profile_id"])) if row["profile_id"] else None,
        "source": row["source"],
    }


def _call(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except (mealplan.MealPlanInputError, mealplan.MealPlanStateError) as exc:
        raise MealRequestError(str(exc)) from exc
    except LookupError as exc:
        raise MealRequestError(str(exc)) from exc


def query_plan(db: Session, params: dict, *, today: date | None = None) -> tuple[dict, bool]:
    """The plan for ``start`` (default: this week's Sunday) and ``days`` (default 7).
    ``date`` alone asks for one day ("what's for dinner today?")."""
    today = today or date.today()
    if params.get("date"):
        start, days = _date(params["date"], "date"), 1
    else:
        start = _date(params["start"], "start") if params.get("start") else mealplan.week_start(today)
        days = _int(params.get("days"), "days", default=7, low=1, high=mealplan.MAX_DAYS)
    week = _call(mealplan.list_week, db, start=start, days=days)
    entries = [_contract_entry(db, row) for row in week["entries"]]
    return {"start": start.isoformat(), "days": days, "entries": entries}, not entries


# Who added it, as the plan records it: the agent channel, not a person.
AGENT_ACTOR = "wildstone"


def add_meal(db: Session, body: dict, *, actor: str | None = AGENT_ACTOR) -> dict:
    row = _call(mealplan.create, db, _entry(db, body), actor=actor, source="agent")
    return {"entry": _contract_entry(db, row)}


def plan_week(db: Session, body: dict, *, actor: str | None = AGENT_ACTOR) -> dict:
    """Draft several meals at once — all of them or, if any is wrong, none."""
    meals = body.get("meals")
    if not isinstance(meals, list) or not meals:
        raise MealRequestError("needs meals: a list of {date, meal_type, recipe | title, ...}")
    if len(meals) > MAX_WEEK_MEALS:
        raise MealRequestError(f"at most {MAX_WEEK_MEALS} meals at once")
    payloads = []
    for index, meal in enumerate(meals):
        try:
            payloads.append(_entry(db, meal))
        except MealRequestError as exc:
            raise MealRequestError(f"meal {index + 1}: {exc}") from exc
    rows = _call(mealplan.create_many, db, payloads, actor=actor, source="agent")
    return {"added": len(rows), "entries": [_contract_entry(db, row) for row in rows]}


def remove_meal(db: Session, body: dict) -> dict:
    """Take a planned meal off the plan. Repeat-safe: removing one already gone
    says so rather than failing."""
    entry_id = uuid_field(body.get("entry_id"), "entry_id")
    try:
        mealplan.remove(db, entry_id)
    except LookupError:
        return {"removed": str(entry_id), "already_removed": True}
    return {"removed": str(entry_id), "already_removed": False}
