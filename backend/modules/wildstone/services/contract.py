"""Contract ``pantry-1``: what Wildstone's adapter may ask and tell (ADR 0026 §2).

Mirrors Wildstone's adapter ``Upstream`` — ``capabilities``, ``query``, ``act``
— and answers in its four result types. Every handler returns ``(data, empty)``;
:func:`answer` wraps that in the envelope. PantryOS answers from its own
database, so nothing here is ever ``stale``.

Names and ``data`` fields of ``pantry.list``, ``pantry.inventory`` and
``pantry.add`` match Wildstone's ``fake-1`` and only ADD fields, so contract
tests written against the fake keep passing against the real thing.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from fastapi.encoders import jsonable_encoder
from sqlalchemy.orm import Session

from modules.agent.services.summary import build_summary
from modules.inventory.services.feed import household_stock
from modules.inventory.services.inventory import list_locations
from modules.planning.services import cook_sessions, cook_timers
from modules.planning.services.insights import list_expiring_items
from modules.planning.services.recipes import get_recipe_with_ingredients, suggest_recipes
from modules.planning.services.steps import split_instructions
from modules.shopping.services import shopping_list
from modules.wildstone.services import meals
from modules.wildstone.services.members import valid_member

CONTRACT_VERSION = "pantry-1"
GROCERY_LISTS = frozenset({"groceries", "grocery", "shopping"})


class UnknownCapability(LookupError):
    """Maps to 404 — the adapter's ErrNoSuchCapability."""


class BadRequest(ValueError):
    """Maps to 422 — the request names a real capability but asks it wrongly."""


def _num(value: Any) -> float:
    return float(Decimal(str(value)).normalize())


def _grocery_list(name: str | None) -> str:
    wanted = (name or "").strip().lower()
    if not wanted:
        raise BadRequest("needs a list name")
    if wanted not in GROCERY_LISTS:
        raise BadRequest(
            f"PantryOS keeps only the grocery list, not {name!r}; other lists belong to Wildstone"
        )
    return "groceries"


# --- queries ---------------------------------------------------------------


def _q_list(db: Session, params: dict) -> tuple[dict, bool]:
    name = _grocery_list(params.get("list"))
    entries = [e for e in shopping_list.list_entries(db, refresh=True) if e["status"] == "pending"]
    data = {
        "list": name,
        "items": [e["name"] for e in entries],
        "entries": [
            {"id": str(e["id"]), "item": e["name"], "quantity": _num(e["quantity"]), "unit": e["unit"], "source": e["source"]}
            for e in entries
        ],
    }
    return data, not entries


def _q_inventory(db: Session, params: dict) -> tuple[dict, bool]:
    rows = household_stock(db, search=params.get("search") or None)
    data = {
        "inventory": [
            {"item": r["item"], "count": _num(r["quantity"]), "unit": r["unit"], "locations": r["locations"]}
            for r in rows
        ]
    }
    return data, not rows


def _q_expiring(db: Session, params: dict) -> tuple[dict, bool]:
    try:
        within = int(params.get("within_days") or 7)
    except ValueError as exc:
        raise BadRequest("within_days must be a whole number") from exc
    if not 0 <= within <= 90:
        raise BadRequest("within_days must be between 0 and 90")
    lots = list_expiring_items(db, location_id=None, within_days=within)
    # Which fridge or freezer it is in: the same item can sit in several.
    places = {loc.id: loc.name for loc in list_locations(db, include_inactive=True)}
    items = [
        {
            "item": lot["item_name"],
            "location": places.get(lot["location_id"]),
            "expires_on": lot["expiration_date"].isoformat(),
            "days_left": lot["days_until_expiration"],
            "quantity": _num(lot["quantity_remaining"]),
            "unit": lot["unit"],
        }
        for lot in lots
    ]
    return {"within_days": within, "items": items}, not items


def _q_summary(db: Session, params: dict) -> tuple[dict, bool]:
    return jsonable_encoder(build_summary(db)), False


def _q_recipes(db: Session, params: dict) -> tuple[dict, bool]:
    suggestions = jsonable_encoder(suggest_recipes(db))
    return {"recipes": suggestions}, not suggestions


def _q_recipe(db: Session, params: dict) -> tuple[dict, bool]:
    try:
        recipe_id = UUID(str(params.get("recipe_id") or ""))
    except ValueError as exc:
        raise BadRequest("needs a recipe_id") from exc
    try:
        recipe, ingredients = get_recipe_with_ingredients(db, recipe_id)
    except LookupError as exc:
        raise BadRequest("no recipe with that id") from exc
    return {
        "recipe_id": str(recipe.id),
        "recipe": recipe.name,
        "servings": recipe.servings,
        "steps": split_instructions(recipe.instructions),
        "ingredients": [
            {"item": i["item_name"], "quantity": _num(i["quantity_required"]), "unit": i["unit"], "optional": i["optional"]}
            for i in ingredients
        ],
    }, False


def _session_data(db: Session, session) -> dict:
    return jsonable_encoder(cook_sessions.view(db, session))


def _q_timers(db: Session, params: dict) -> tuple[dict, bool]:
    timers = jsonable_encoder(cook_timers.list_timers(db))
    return {"timers": timers}, not timers


def _q_cook_current(db: Session, params: dict) -> tuple[dict, bool]:
    session = cook_sessions.get_active(db)
    if session is None:
        return {"session": None}, True
    return {"session": _session_data(db, session)}, False


# --- actions ---------------------------------------------------------------


def _a_add(db: Session, body: dict) -> tuple[dict, bool]:
    name = _grocery_list(body.get("list"))
    item = (body.get("item") or "").strip()
    if not item:
        raise BadRequest("needs a list and an item")
    entry, added = shopping_list.add_entry_once(db, name=item, source="agent")
    data = {"list": name, "item": entry["name"], "added": added, "entry_id": str(entry["id"])}
    if not added:
        data["reason"] = "already on the list"
    return data, False


def _a_check(db: Session, body: dict) -> tuple[dict, bool]:
    name = _grocery_list(body.get("list"))
    item = (body.get("item") or "").strip()
    if not item:
        raise BadRequest("needs a list and an item")
    entry = shopping_list.check_off_by_name(db, name=item)
    if entry is None:
        return {"list": name, "item": item, "checked": False, "reason": "not on the list"}, False
    return {"list": name, "item": entry["name"], "checked": True}, False


def _active_or_fail(db: Session):
    session = cook_sessions.get_active(db)
    if session is None:
        raise BadRequest("nothing is being cooked; start a recipe first")
    return session


def _a_cook_start(db: Session, body: dict) -> tuple[dict, bool]:
    try:
        recipe_id = UUID(str(body.get("recipe_id") or ""))
    except ValueError as exc:
        raise BadRequest("needs a recipe_id") from exc
    servings = body.get("servings")
    try:
        session = cook_sessions.start_session(
            db, recipe_id=recipe_id, servings_made=Decimal(str(servings)) if servings is not None else None
        )
    except LookupError as exc:
        raise BadRequest("no recipe with that id") from exc
    except ValueError as exc:
        raise BadRequest(str(exc)) from exc
    return {"session": _session_data(db, session)}, False


def _a_cook_step(db: Session, body: dict) -> tuple[dict, bool]:
    session = _active_or_fail(db)
    try:
        session = cook_sessions.move_step(
            db, session_id=session.id, action=str(body.get("action") or ""), step=body.get("step")
        )
    except ValueError as exc:
        raise BadRequest(str(exc)) from exc
    return {"session": _session_data(db, session)}, False


def _a_cook_finish(db: Session, body: dict) -> tuple[dict, bool]:
    """Finish the cook. Repeat-safe: a retry after a lost response — no cook
    active any more — answers with the cook just recorded instead of failing,
    and a caller that names its ``session_id`` always gets that session's."""
    location = body.get("location_id")
    try:
        if body.get("session_id"):
            session_id = UUID(str(body["session_id"]))
        else:
            session = cook_sessions.get_active(db) or cook_sessions.recently_finished(db)
            if session is None:
                raise BadRequest("nothing is being cooked; start a recipe first")
            session_id = session.id
        location_id = UUID(str(location)) if location else None
        session, result = cook_sessions.finish_session(db, session_id=session_id, location_id=location_id)
    except (ValueError, LookupError) as exc:
        raise BadRequest(str(exc)) from exc
    return {"session": _session_data(db, session), "cook": jsonable_encoder(result)}, False


def _a_timer_set(db: Session, body: dict) -> tuple[dict, bool]:
    """A timer, tied to the cook in progress (and its step) when there is one."""
    try:
        seconds = int(body.get("seconds") or 0)
    except (TypeError, ValueError) as exc:
        raise BadRequest("seconds must be a whole number") from exc
    session = cook_sessions.get_active(db)
    step = None
    if session is not None:
        step = body.get("step_number") or cook_sessions.view(db, session)["step_number"] or None
    try:
        timer = cook_timers.start_timer(
            db,
            seconds=seconds,
            label=body.get("label"),
            session_id=session.id if session else None,
            step_number=step,
            # The member the agent is acting for: "your timer is done" goes
            # back to them (pantry.timer_finished's subject).
            set_by_member=valid_member(body.get("member")),
        )
    except (ValueError, LookupError) as exc:
        raise BadRequest(str(exc)) from exc
    return {"timer": jsonable_encoder(cook_timers.view(timer))}, False


def _a_timer_cancel(db: Session, body: dict) -> tuple[dict, bool]:
    timer_id = body.get("timer_id")
    try:
        stopped = cook_timers.stop_timer(
            db, timer_id=UUID(str(timer_id)) if timer_id else None, label=body.get("label")
        )
    except (ValueError, LookupError) as exc:
        raise BadRequest(str(exc)) from exc
    return {"timer": jsonable_encoder(stopped)}, False


def _a_cook_abandon(db: Session, body: dict) -> tuple[dict, bool]:
    session = cook_sessions.get_active(db)
    if session is None:
        return {"session": None}, True
    return {"session": _session_data(db, cook_sessions.abandon_session(db, session_id=session.id))}, False


def _a_meal_log(db: Session, body: dict) -> tuple[dict, bool]:
    """ "I had two eggs" (#79): the member who ate, the food by name."""
    try:
        return {"meal": meals.log_meal(db, body)}, False
    except meals.MealRequestError as exc:
        raise BadRequest(str(exc)) from exc


def _a_meal_undo(db: Session, body: dict) -> tuple[dict, bool]:
    try:
        return meals.undo_meal(db, body), False
    except meals.MealRequestError as exc:
        raise BadRequest(str(exc)) from exc


Handler = Callable[[Session, dict], tuple[dict, bool]]

QUERIES: dict[str, Handler] = {
    "pantry.list": _q_list,
    "pantry.inventory": _q_inventory,
    "pantry.expiring": _q_expiring,
    "pantry.summary": _q_summary,
    "pantry.recipes": _q_recipes,
    "pantry.recipe": _q_recipe,
    "pantry.cook.current": _q_cook_current,
    "pantry.timers": _q_timers,
}

ACTIONS: dict[str, Handler] = {
    "pantry.add": _a_add,
    "pantry.check": _a_check,
    "pantry.cook.start": _a_cook_start,
    "pantry.cook.step": _a_cook_step,
    "pantry.cook.finish": _a_cook_finish,
    "pantry.cook.abandon": _a_cook_abandon,
    "pantry.timer.set": _a_timer_set,
    "pantry.timer.cancel": _a_timer_cancel,
    "pantry.meal.log": _a_meal_log,
    "pantry.meal.undo": _a_meal_undo,
}

# A PROPOSAL (ADR-W030 A8): Wildstone's policy validates it, and an owner may
# only make it stricter.
PROPOSED_RISK: dict[str, str] = {
    "pantry.add": "domestic",
    "pantry.check": "domestic",
    "pantry.cook.start": "read",
    "pantry.cook.step": "read",
    "pantry.cook.finish": "confirm",
    "pantry.cook.abandon": "read",
    # A timer changes nothing in the household's data; like moving a step.
    "pantry.timer.set": "read",
    "pantry.timer.cancel": "read",
    # Your own log only, and undo exists.
    "pantry.meal.log": "domestic",
    "pantry.meal.undo": "domestic",
}


def capabilities() -> dict:
    caps = [{"kind": "query", "name": name, "version": CONTRACT_VERSION, "available": True} for name in QUERIES]
    caps += [
        {
            "kind": "action",
            "name": name,
            "version": CONTRACT_VERSION,
            "available": True,
            "proposedRisk": PROPOSED_RISK[name],
        }
        for name in ACTIONS
    ]
    caps += [
        {"kind": "event", "name": name, "version": CONTRACT_VERSION, "available": True}
        for name in (
            "pantry.meal_logged",
            "pantry.purchase_recorded",
            "pantry.recipe_cooked",
            "pantry.stock_low",
            "pantry.item_expiring",
            "pantry.item_spoiled",
            "pantry.shopping_list_changed",
            "pantry.timer_finished",
        )
    ]
    return {"contract": CONTRACT_VERSION, "capabilities": caps}


def answer(capability: str, data: dict, empty: bool) -> dict:
    return {
        "contract": CONTRACT_VERSION,
        "capability": capability,
        "status": "empty" if empty else "ok",
        "observedAt": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "data": data,
    }


def query(db: Session, capability: str, params: dict) -> dict:
    handler = QUERIES.get(capability)
    if handler is None:
        raise UnknownCapability(capability)
    data, empty = handler(db, params)
    return answer(capability, data, empty)


def act(db: Session, capability: str, body: dict) -> dict:
    handler = ACTIONS.get(capability)
    if handler is None:
        raise UnknownCapability(capability)
    data, empty = handler(db, body)
    return answer(capability, data, empty)
