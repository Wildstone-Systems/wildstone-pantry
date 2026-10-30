"""Server-side cook sessions: step-by-step cooking an agent can drive (ADR 0026 §6).

A session is progress only — which recipe, how many servings, which step. It
never touches stock: ``finish_session`` hands off to :func:`cook_recipe`, so
ADR 0022's scaling rules and all-or-nothing deduction apply unchanged, and
abandoning costs nothing.

One session is active at a time (a household cooks one thing at once as far as
"next step" is concerned); starting another abandons the old one. A session
untouched for :data:`STALE_AFTER` is treated as abandoned rather than resumed
days later.

The session is the ONE place the cook-along lives (ADR 0026 §6.1): the kitchen
tablet and an agent read and move the same step, tick off the same steps, see
the same timers, and finish with the same plan — so "next" said to the agent
moves the tablet, and a tap on the tablet is what the agent reads back.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from uuid import UUID

from fastapi.encoders import jsonable_encoder
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from modules.catalog.services.items import get_item
from modules.inventory.services.feed import head_seq, recipe_deductions_after
from modules.planning import repository
from modules.planning.models import CookSession
from modules.planning.services import cook_timers
from modules.planning.services.recipes import _scale_from_ingredient, cook_recipe, get_recipe_with_ingredients
from modules.planning.services.steps import split_instructions
from modules.planning.services.substitutions import cook_basis, saved_ratio

STALE_AFTER = timedelta(hours=12)
# How many times a write retries after losing a race with another writer (the
# tablet and an agent at once) before giving up with "try again".
MAX_RETRIES = 5
# "complete" ticks off the current step and moves on: what a cook means by
# "done, what's next?".
STEP_ACTIONS = ("next", "previous", "repeat", "goto", "complete")


def _now() -> datetime:
    return datetime.now(UTC)


def _as_utc(value: datetime) -> datetime:
    # SQLite hands timestamps back naive; they were written as UTC.
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def _trim(value: Decimal) -> str:
    text = format(value.quantize(Decimal("0.01")).normalize(), "f")
    return text


def _scale(recipe_servings: int | None, servings_made: Decimal | None) -> Decimal:
    if servings_made is None:
        return Decimal("1")
    return Decimal(servings_made) / Decimal(recipe_servings or 1)


def _plan_scale(db: Session, recipe, ingredients: list[dict], session: CookSession) -> Decimal:
    """The multiplier Finish will cook at: "use it all" when the plan says so
    (sized from what is on hand now, exactly as ``cook_recipe`` sizes it),
    otherwise the servings asked for."""
    scale_to = (session.plan or {}).get("scale_to_ingredient_id")
    if scale_to:
        lines = [
            SimpleNamespace(
                id=line["ingredient_id"],
                item_id=line["item_id"],
                quantity_required=line["quantity_required"],
                unit=line["unit"],
                quantity_mode=line.get("quantity_mode", "amount"),
            )
            for line in ingredients
        ]
        try:
            return _scale_from_ingredient(db, ingredients=lines, ingredient_id=UUID(str(scale_to)), location_id=None)
        except (LookupError, ValueError):
            # Nothing on hand to size from any more: Finish will say so; until
            # then show the recipe at its servings rather than nothing.
            pass
    return _scale(recipe.servings, session.servings_made)


def _swaps(db: Session, ingredients: list[dict], plan: dict | None, scoped=None) -> dict[str, dict]:
    """The plan's substitutions, per ingredient line, as Finish will apply them:
    the substitute's name, the ratio (the one given, else the one learned, else
    1) and the unit (the one given, else the line's own)."""
    by_original = {str(s.get("original_item_id")): s for s in (plan or {}).get("substitutions") or []}
    out: dict[str, dict] = {}
    for line in ingredients:
        swap = by_original.get(str(line["item_id"]))
        if swap is None:
            continue
        substitute_id = UUID(str(swap["substitute_item_id"]))
        substitute = get_item(db, substitute_id)
        bound = cook_basis(db, UUID(str(line["item_id"])), substitute_id, line["unit"])
        ratio = swap.get("ratio")
        if ratio is None:
            ratio = bound.ratio if bound else saved_ratio(db, UUID(str(line["item_id"])), substitute_id, original_unit=line["unit"])
        out[str(line["ingredient_id"])] = {
            "item": substitute.name if substitute is not None else line["item_name"],
            "ratio": Decimal(str(ratio)) if ratio is not None else Decimal("1"),
            "unit": swap.get("unit") or (bound.unit if bound else line["unit"]),
        }
    for choice in scoped or []:
        out[choice["ingredient_id"]] = {"item": choice["to_name"], "ratio": Decimal(choice["ratio"]), "unit": choice["unit"]}
    return out


def _scaled_ingredients(
    ingredients: list[dict], scale: Decimal, plan: dict | None = None, swaps: dict[str, dict] | None = None
) -> list[dict]:
    """The amounts the cook will use — what Finish will take out.

    The plan wins over the arithmetic (ADR 0022 §2): an omitted line is not
    listed, an overridden amount is shown as typed, not scaled again, and a
    substituted line shows the substitute at the substitution's ratio.
    """
    overrides = {str(o.get("ingredient_id")): o for o in (plan or {}).get("ingredient_overrides") or []}
    swaps = swaps or {}
    out = []
    for line in ingredients:
        override = overrides.get(str(line["ingredient_id"])) or {}
        if override.get("omit"):
            continue
        swap = swaps.get(str(line["ingredient_id"]))
        quantity = Decimal(line["quantity_required"])
        unit = line["unit"]
        if swap is not None:
            quantity, unit = quantity * swap["ratio"], swap["unit"]
        if override.get("quantity") is not None:
            quantity = Decimal(str(override["quantity"]))
            unit = override.get("unit") or unit
        # A whole-package line ("one roast, whatever it weighs") does not scale
        # by arithmetic (ADR 0025 §5); show it as written.
        elif line.get("quantity_mode", "amount") == "amount":
            quantity = quantity * scale
        entry = {
            "ingredient_id": str(line["ingredient_id"]),
            "item": swap["item"] if swap is not None else line["item_name"],
            "quantity": _trim(quantity),
            "unit": unit,
            "optional": bool(line.get("optional")),
        }
        if swap is not None:
            entry["replaces"] = line["item_name"]
        out.append(entry)
    return out


def _mentioned_in(step_text: str, ingredients: list[dict]) -> list[dict]:
    """Ingredients whose name appears in the step's text.

    A heuristic, not a link: steps are derived text (ADR 0022 §10), so there is
    nothing structured to join on. Singular/plural is the only fuzziness allowed
    — anything looser starts matching "oil" inside "boil".
    """
    text = f" {step_text.lower()} "
    found = []
    for line in ingredients:
        # A substituted line is still found by the name the recipe text uses.
        candidates = set()
        for name in filter(None, (line["item"], line.get("replaces"))):
            name = name.lower().strip()
            candidates |= {name, name.rstrip("s"), f"{name}s"}
        if any(f" {c} " in text or f" {c}," in text or f" {c}." in text or f" {c};" in text for c in candidates if c):
            found.append(line)
    return found


def _expire_if_stale(db: Session, session: CookSession) -> bool:
    if session.status == "active" and _now() - _as_utc(session.updated_at) > STALE_AFTER:
        session.status = "abandoned"
        session.ended_at = _now()
        repository.add(db, session)
        db.commit()
        return True
    return False


def _completed(session: CookSession, total: int) -> list[int]:
    return sorted({int(n) for n in (session.completed_steps or []) if 1 <= int(n) <= total})


def view(db: Session, session: CookSession) -> dict:
    """The session as a person (or an agent reading aloud) needs it."""
    history = (session.result or {}).get("_scoped_history")
    if session.status == "finished" and history:
        return {**history["view"], "status": "finished", "cook_event_id": session.cook_event_id,
                "updated_at": session.updated_at, "scoped_substitutions": history["choices"]}
    from modules.planning.services.substitution_reviews import resolve_attached
    warning = None
    try:
        scoped = resolve_attached(db, session)
    except (LookupError, ValueError) as exc:
        scoped, warning = [], str(exc)
    recipe, ingredients = get_recipe_with_ingredients(db, session.recipe_id)
    steps = split_instructions(recipe.instructions)
    try:
        swaps = _swaps(db, ingredients, session.plan, scoped)
    except (LookupError, ValueError) as exc:
        swaps, warning = {}, str(exc)
    scaled = _scaled_ingredients(
        ingredients,
        _plan_scale(db, recipe, ingredients, session),
        session.plan,
        swaps,
    )
    total = len(steps)
    index = min(session.current_step, max(total - 1, 0))
    step_text = steps[index] if steps else ""
    done = _completed(session, total)
    return {
        "id": session.id,
        "scoped_substitutions": scoped,
        "substitution_review_warning": warning,
        "recipe_id": recipe.id,
        "recipe_name": recipe.name,
        "status": session.status,
        "base_servings": recipe.servings,
        "servings_made": session.servings_made,
        "step_number": index + 1 if steps else 0,
        "total_steps": total,
        "step_text": step_text,
        "is_first_step": index == 0,
        "is_last_step": total == 0 or index == total - 1,
        "step_ingredients": _mentioned_in(step_text, scaled) if step_text else [],
        "step_done": (index + 1) in done,
        # Timers the step's own words suggest ("simmer 20 minutes"); offered,
        # never started on their own.
        "step_timers": cook_timers.suggested_timers(step_text),
        "steps": [{"number": i + 1, "text": text, "done": (i + 1) in done} for i, text in enumerate(steps)],
        "completed_steps": done,
        "steps_left": total - len(done),
        "timers": cook_timers.list_timers(db),
        "plan": session.plan,
        "ingredients": scaled,
        "cook_event_id": session.cook_event_id,
        "started_at": session.started_at,
        "updated_at": session.updated_at,
    }


def start_session(
    db: Session,
    *,
    recipe_id: UUID,
    servings_made: Decimal | None = None,
    actor: str | None = None,
    plan: dict | None = None,
) -> CookSession:
    # Raises LookupError for an unknown recipe before anything is abandoned.
    get_recipe_with_ingredients(db, recipe_id)
    if (plan or {}).get("substitution_approval_ids"):
        raise ValueError("Start the cook first, then explicitly attach a reviewed substitution")
    if servings_made is not None and Decimal(servings_made) <= 0:
        raise ValueError("servings must be more than zero")

    for _ in range(MAX_RETRIES):
        now = _now()
        _abandon_active(db, now)
        session = CookSession(
            recipe_id=recipe_id,
            servings_made=servings_made,
            current_step=0,
            completed_steps=[],
            plan=plan,
            status="active",
            actor=actor,
            version=0,
            started_at=now,
            updated_at=now,
        )
        repository.add(db, session)
        try:
            db.commit()
        except IntegrityError:
            # Another start (the tablet and an agent at once) got its session
            # in between; the unique "one active" index refused ours. Abandon
            # that one too and try again — the last start wins, as it would
            # one after the other.
            db.rollback()
            continue
        db.refresh(session)
        return session
    raise ValueError("Several cooks were started at once; try again")


def _abandon_active(db: Session, now: datetime) -> None:
    repository.abandon_active_sessions(db, now=now)


def get_session(db: Session, session_id: UUID) -> CookSession:
    session = repository.get_cook_session(db, session_id)
    if session is None:
        raise LookupError("Cook session not found")
    _expire_if_stale(db, session)
    return session


def get_active(db: Session) -> CookSession | None:
    session = repository.latest_active_session(db)
    if session is None or _expire_if_stale(db, session):
        return None
    return session


def recently_finished(db: Session, *, within: timedelta = timedelta(minutes=5)) -> CookSession | None:
    """The cook finished most recently, if it ended within ``within`` — what
    a Finish retried after a lost response is asking about."""
    return repository.latest_finished_session(db, ended_after=_now() - within)


def _require_active(session: CookSession) -> None:
    if session.status != "active":
        raise ValueError(f"This cook session is {session.status}; start a new one")


def _change(db: Session, session_id: UUID, decide) -> CookSession:
    """Apply one change to an active session without losing anyone else's.

    ``decide(session)`` reads the session as it is now and returns the
    columns to set. The write lands only on the version that was read (one
    conditional UPDATE); if the tablet or an agent changed the session in
    between, it is read again and decided again, so two ticks at once both
    stick instead of one overwriting the other.
    """
    for _ in range(MAX_RETRIES):
        session = get_session(db, session_id)
        db.refresh(session)
        _require_active(session)
        values = decide(session)
        read_version = session.version or 0
        landed = repository.update_active_session_at_version(
            db, session_id=session_id, version=read_version, values=values, now=_now()
        )
        db.commit()
        if landed:
            db.refresh(session)
            return session
    raise ValueError("The cook changed several times at once; try again")


_UNSET = object()


def update_session(
    db: Session, *, session_id: UUID, servings_made=_UNSET, plan=_UNSET
) -> CookSession:
    """Change how an active cook is set up — the servings, or the plan (the
    person went back to the ingredient list mid-cook). Progress is kept."""
    if servings_made is not _UNSET and servings_made is not None and Decimal(servings_made) <= 0:
        raise ValueError("servings must be more than zero")

    def decide(_session: CookSession) -> dict:
        values = {}
        if servings_made is not _UNSET:
            values["servings_made"] = servings_made
        if plan is not _UNSET:
            from modules.planning.services.substitution_reviews import preserve_plan_bindings
            values["plan"] = preserve_plan_bindings(_session, plan)
        return values

    return _change(db, session_id, decide)


def _step_count(db: Session, session: CookSession) -> int:
    recipe, _ = get_recipe_with_ingredients(db, session.recipe_id)
    return len(split_instructions(recipe.instructions))


def set_step_done(db: Session, *, session_id: UUID, step: int, done: bool = True) -> CookSession:
    """Tick a step off (or back on) without moving — the tablet's checkbox."""

    def decide(session: CookSession) -> dict:
        total = _step_count(db, session)
        if step < 1 or step > total:
            raise ValueError(f"step must be between 1 and {total}")
        marked = set(_completed(session, total))
        if done:
            marked.add(step)
        else:
            marked.discard(step)
        return {"completed_steps": sorted(marked)}

    return _change(db, session_id, decide)


def move_step(db: Session, *, session_id: UUID, action: str, step: int | None = None) -> CookSession:
    """Move through the steps. ``goto`` takes a 1-based step number.

    Moving past either end is clamped rather than refused: "next" on the last
    step answers with the last step (and ``is_last_step``), which is what a
    person saying "next" at the end actually wants to hear.

    ``complete`` ticks off a step — the current one, or ``step`` — and moves
    to the next step not yet ticked off after it (or stays, on the last).
    """
    if action not in STEP_ACTIONS:
        raise ValueError(f"action must be one of {', '.join(STEP_ACTIONS)}")

    def decide(session: CookSession) -> dict:
        last = max(_step_count(db, session) - 1, 0)
        values: dict = {}
        if action == "next":
            target = session.current_step + 1
        elif action == "previous":
            target = session.current_step - 1
        elif action == "goto":
            if step is None:
                raise ValueError("goto needs a step number")
            if step < 1 or step > last + 1:
                raise ValueError(f"step must be between 1 and {last + 1}")
            target = step - 1
        elif action == "complete":
            number = step if step is not None else session.current_step + 1
            if number < 1 or number > last + 1:
                raise ValueError(f"step must be between 1 and {last + 1}")
            marked = set(_completed(session, last + 1)) | {number}
            values["completed_steps"] = sorted(marked)
            target = next((i for i in range(number, last + 1) if (i + 1) not in marked), min(number, last))
        else:  # repeat
            target = session.current_step
        values["current_step"] = min(max(target, 0), last)
        return values

    return _change(db, session_id, decide)


def finish_session(
    db: Session,
    *,
    session_id: UUID,
    location_id: UUID | None = None,
    actor: str | None = None,
    output: dict | None = None,
) -> tuple[CookSession, dict]:
    """Cook it for real: deduct stock through the ordinary cook path.

    The session's plan decides WHAT is cooked — its ingredient overrides,
    "use it all" and "use what I have" — whoever finishes it, so an agent
    finishing a cook set up on the tablet cooks what was set up there.
    ``output`` is what only the finish knows: what the dish weighed and how it
    was packed (ADR 0025 §3/§4).

    The session is CLAIMED first — one conditional UPDATE from ``active`` to
    ``finishing`` — so two overlapping finishes (a double tap, a retry racing a
    slow first request) cannot both reach ``cook_recipe``, which commits its
    deductions on its own. Only the caller whose UPDATE matched cooks.

    Finishing again once finished is not an error: it answers with the cook
    already recorded, so a retry after a lost response is safe. If the cook
    fails before touching stock (not enough of it, portions that don't fit),
    the claim is released and the session is active again. A failure after
    stock came out — or a crash — leaves the session ``finishing``: stuck, but
    never deducted twice.
    """
    get_session(db, session_id)  # 404 for an unknown session, stale expiry
    claimed = repository.claim_session_for_finish(db, session_id=session_id, now=_now())
    db.commit()

    if not claimed:
        session = get_session(db, session_id)
        db.refresh(session)
        if session.status == "finished":
            return session, {**(session.result or {}), "cook_event_id": session.cook_event_id, "already_finished": True}
        if session.status == "finishing":
            raise ValueError("This cook session is already being finished")
        _require_active(session)

    session = get_session(db, session_id)
    plan = session.plan or {}
    output = output or {}
    scale_to = plan.get("scale_to_ingredient_id")
    before_seq = head_seq(db)
    try:
        from modules.planning.services.substitution_reviews import consume, resolve_attached
        scoped = resolve_attached(db, session, lock=True)
        historical_view = view(db, session) if scoped else None
        result = cook_recipe(
            db,
            recipe_id=session.recipe_id,
            location_id=location_id,
            servings=1,
            leftover_servings=0,
            note=None,
            actor=actor or session.actor,
            # Sizing from one ingredient IS the batch size (ADR 0025 §5), so a
            # serving count alongside it would be two answers to one question.
            servings_made=None if scale_to else session.servings_made,
            scale_to_ingredient_id=UUID(str(scale_to)) if scale_to else None,
            allow_short_ingredients=bool(plan.get("allow_short_ingredients")),
            ingredient_overrides=_overrides(plan),
            substitutions=plan.get("substitutions") or None,
            scoped_substitutions=scoped,
            output_weight_grams=_decimal(output.get("output_weight_grams")),
            output_portions=output.get("output_portions"),
            output_portion_size=_decimal(output.get("output_portion_size")),
            output_portion_unit=output.get("output_portion_unit"),
        )
    except Exception:
        db.rollback()
        if recipe_deductions_after(db, recipe_id=session.recipe_id, after_seq=before_seq):
            # Stock already came out: releasing the claim would let a second
            # Finish take it out again. The session stays ``finishing``.
            raise ValueError(
                "The cook stopped part-way after taking stock out; check the pantry "
                "before cooking this again"
            ) from None
        session = get_session(db, session_id)
        session.status = "active"
        repository.add(db, session)
        db.commit()
        raise
    if scoped:
        result["_scoped_history"] = {"view": jsonable_encoder(historical_view), "choices": scoped}
        consume(db, scoped)
    session.status = "finished"
    session.cook_event_id = result["cook_event_id"]
    session.result = jsonable_encoder(result)
    session.ended_at = session.updated_at = _now()
    repository.add(db, session)
    db.commit()
    db.refresh(session)
    return session, result


def _decimal(value) -> Decimal | None:
    return None if value is None else Decimal(str(value))


def _overrides(plan: dict) -> list[dict] | None:
    out = []
    for o in plan.get("ingredient_overrides") or []:
        out.append(
            {
                "ingredient_id": UUID(str(o["ingredient_id"])),
                "quantity": _decimal(o.get("quantity")),
                "unit": o.get("unit"),
                "omit": bool(o.get("omit")),
            }
        )
    return out or None


def abandon_session(db: Session, *, session_id: UUID) -> CookSession:
    session = get_session(db, session_id)
    if session.status == "active":
        session.status = "abandoned"
        session.ended_at = session.updated_at = _now()
        repository.add(db, session)
        db.commit()
        db.refresh(session)
    return session
