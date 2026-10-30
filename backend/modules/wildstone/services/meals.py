"""Meal logging for agents: "I had two eggs" (ADR 0026 §5, #79).

An agent speaks in members and food names; plating needs a profile and an
item. This resolves the two — and when a food name is not certain, it asks
(the caller gets the candidates back) rather than guessing: logging the wrong
food is worse than one more question.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from uuid import UUID

from sqlalchemy.orm import Session

from app.services.profiles import resolve_actor_profile
from modules.catalog.services.barcodes import get_or_create_item_from_food_search, search_food_by_name
from modules.catalog.services.canonical import canonical_ingredient
from modules.catalog.services.items import get_item, list_items
from modules.inventory.services.consumption import delete_plate, plate
from modules.inventory.services.feed import fullest_location
from modules.wildstone import repository

# How many candidates an uncertain food name offers back.
MAX_CANDIDATES = 5


class MealRequestError(ValueError):
    """The request can't be carried out as asked; the message says why, and for
    an uncertain food, which foods it could be."""


def _uuid(value, field: str) -> UUID:
    try:
        return UUID(str(value))
    except ValueError as exc:
        raise MealRequestError(f"{field} must be an id") from exc


def _profile(db: Session, body: dict) -> str:
    if body.get("profile_id"):
        # The same check the plate route makes: an unknown or inactive
        # profile would log a meal nobody can see.
        profile = resolve_actor_profile(db, str(_uuid(body["profile_id"], "profile_id")))
        if profile is None:
            raise MealRequestError("no active household profile has that profile_id")
        return profile
    member = str(body.get("member") or "").strip()
    if not member:
        raise MealRequestError("needs a member (who ate) or a profile_id")
    for profile_id, linked in repository.member_links(db).items():
        if linked == member:
            return str(profile_id)
    raise MealRequestError(
        f"no PantryOS profile is linked to {member!r}; link one in PantryOS (Admin → Wildstone → Who is who)"
    )


def _food(db: Session, body: dict):
    """The catalog item to log — certain, or a MealRequestError naming the
    candidates to choose from (then sent back as item_id or fdc_id)."""
    food = str(body.get("food") or "").strip()
    if body.get("item_id"):
        item = get_item(db, _uuid(body["item_id"], "item_id"))
        if item is None:
            raise MealRequestError("no food with that item_id")
        return item
    if body.get("fdc_id") is not None:
        if not food:
            raise MealRequestError("an fdc_id needs the food name it was found by")
        try:
            return get_or_create_item_from_food_search(db, query=food, fdc_id=int(body["fdc_id"]))
        except (LookupError, ValueError) as exc:
            raise MealRequestError(f"that fdc_id could not be used: {exc}") from exc
    if not food:
        raise MealRequestError("needs a food")

    # Certain only when exactly one food is named exactly so, or exactly one
    # food fits at all. A canonical "same ingredient" match is NOT certain —
    # two brands of parmesan are both parmesan — so it is offered, not taken.
    matches = list_items(db, search=food)
    # The same ingredient under other names ("eggs" -> "Egg", "Large eggs"):
    # candidates too, never a silent pick.
    key = canonical_ingredient(food)
    if key:
        known = {i.id for i in matches}
        matches += [i for i in list_items(db, search=key) if i.canonical_ingredient == key and i.id not in known]
    exact = [i for i in matches if i.name.strip().lower() == food.lower()]
    if len(exact) == 1:
        return exact[0]
    if len(matches) == 1:
        return matches[0]
    if matches:
        shown = sorted(matches, key=lambda i: len(i.name))[:MAX_CANDIDATES]
        options = "; ".join(f"{i.name} (item_id {i.id})" for i in shown)
        raise MealRequestError(f"which {food!r}? {options} — send the item_id")
    found = search_food_by_name(db, food, limit=MAX_CANDIDATES).get("candidates") or []
    if not found:
        raise MealRequestError(f"PantryOS knows no food called {food!r}")
    options = "; ".join(f"{c['description']} (fdc_id {c['fdc_id']})" for c in found[:MAX_CANDIDATES])
    raise MealRequestError(f"{food!r} is not in the pantry's foods; did you mean: {options} — send the fdc_id")


def _quantity(body: dict) -> Decimal:
    raw = body.get("quantity")
    if raw is None or raw == "":
        return Decimal("1")
    try:
        quantity = Decimal(str(raw))
    except InvalidOperation as exc:
        raise MealRequestError("quantity must be a number") from exc
    if not quantity.is_finite() or quantity <= 0:
        raise MealRequestError("quantity must be above zero")
    return quantity


def log_meal(db: Session, body: dict) -> dict:
    """Plate a food for a member, taken from stock when there is some."""
    profile_id = _profile(db, body)
    item = _food(db, body)
    quantity = _quantity(body)
    unit = str(body.get("unit") or "each").strip() or "each"
    location_id = _uuid(body["location_id"], "location_id") if body.get("location_id") else fullest_location(db, item.id)
    try:
        event_id, row, item, eaten, calories, *_macros = plate(
            db,
            item_id=item.id,
            location_id=location_id,
            profile_id=profile_id,
            quantity=quantity,
            unit=unit,
            meal_type=(str(body["meal_type"]).strip()[:24] or None) if body.get("meal_type") else None,
        )
    except (LookupError, ValueError) as exc:
        raise MealRequestError(str(exc)) from exc
    return {
        "event_id": str(event_id),
        "food": item.name,
        "quantity": float(eaten),
        "unit": unit,
        "calories": float(calories) if calories is not None else None,
        "from_stock": location_id is not None,
    }


def undo_meal(db: Session, body: dict) -> dict:
    """Take back a logged meal (stock it used goes back). Repeat-safe: undoing
    one already undone says so rather than failing."""
    event_id = _uuid(body.get("event_id"), "event_id")
    try:
        delete_plate(db, event_id=event_id)
    except LookupError as exc:
        raise MealRequestError("no logged meal with that event_id") from exc
    except ValueError:
        return {"undone": str(event_id), "already_undone": True}
    return {"undone": str(event_id), "already_undone": False}
