"""Kitchen timers for the cook-along (ADR 0026 §6.1).

A timer is set by a tap on the tablet or by an agent ("set a timer for the
pasta"), and whoever is in the kitchen sees and hears it on the tablet. It is
usually tied to the cook session and step it was set in, but it outlives the
session: stopping the cook-along does not un-boil the pasta.

"Ringing" is derived, never stored: a running timer whose end has passed. A
ringing timer nobody dismisses stops showing after :data:`RING_FOR`, so a
forgotten timer does not ring on the tablet for days.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy.orm import Session

from modules.planning import repository
from modules.planning.models import CookTimer

MAX_SECONDS = 24 * 60 * 60
MAX_RUNNING = 10
RING_FOR = timedelta(hours=1)


def _now() -> datetime:
    return datetime.now(UTC)


def _as_utc(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def spoken_duration(seconds: int) -> str:
    """"1 hr 5 min", "25 min", "45 sec" — for labels and for reading aloud."""
    hours, rest = divmod(int(seconds), 3600)
    minutes, secs = divmod(rest, 60)
    parts = []
    if hours:
        parts.append(f"{hours} hr")
    if minutes:
        parts.append(f"{minutes} min")
    if secs and not hours:
        parts.append(f"{secs} sec")
    return " ".join(parts) or "0 sec"


# --- durations in step text --------------------------------------------------

_WORDS = {
    "a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
    "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "fifteen": 15, "twenty": 20,
    "thirty": 30, "forty": 40, "forty-five": 45, "fifty": 50, "sixty": 60, "ninety": 90,
}
_FRACTIONS = {"½": 0.5, "¼": 0.25, "¾": 0.75, "⅓": 1 / 3, "⅔": 2 / 3}
_NUMBER = r"(?:\d+(?:\.\d+)?\s*[½¼¾⅓⅔]?|[½¼¾⅓⅔]|" + "|".join(sorted(_WORDS, key=len, reverse=True)) + r")"
_UNIT_SECONDS = {"s": 1, "m": 60, "h": 3600}
_DURATION = re.compile(
    rf"\b(?P<low>{_NUMBER})(?:\s*(?:-|–|to|or)\s*(?P<high>{_NUMBER}))?\s*"
    r"(?P<unit>seconds?|secs?|minutes?|mins?|hours?|hrs?)\b",
    re.IGNORECASE,
)


def _number(text: str) -> float | None:
    text = text.strip().lower()
    if text in _WORDS:
        return float(_WORDS[text])
    value = 0.0
    for char, frac in _FRACTIONS.items():
        if char in text:
            value += frac
            text = text.replace(char, "")
    text = text.strip()
    if text:
        try:
            value += float(text)
        except ValueError:
            return None
    return value or None


def suggested_timers(step_text: str) -> list[dict]:
    """Timers a step's own words suggest: "simmer for 20-25 minutes" → 20 min.

    A range suggests its LOW end — check early rather than burn it — and the
    label keeps the range so the cook knows it was one. At most three, in the
    order they appear, without repeats. A heuristic over free text, like the
    ingredient mentions: it offers, it never starts anything on its own.
    """
    out: list[dict] = []
    seen: set[int] = set()
    for match in _DURATION.finditer(step_text or ""):
        low = _number(match.group("low"))
        if low is None:
            continue
        seconds = int(round(low * _UNIT_SECONDS[match.group("unit")[0].lower()]))
        if seconds <= 0 or seconds > MAX_SECONDS or seconds in seen:
            continue
        seen.add(seconds)
        out.append({"seconds": seconds, "label": match.group(0).strip()})
        if len(out) == 3:
            break
    return out


# --- timers -------------------------------------------------------------------


def view(timer: CookTimer, now: datetime | None = None) -> dict:
    now = now or _now()
    ends_at = _as_utc(timer.ends_at)
    remaining = max(0, int((ends_at - now).total_seconds() + 0.999))
    return {
        "id": timer.id,
        "label": timer.label,
        "session_id": timer.session_id,
        "step_number": timer.step_number,
        "duration_seconds": timer.duration_seconds,
        "started_at": _as_utc(timer.started_at),
        "ends_at": ends_at,
        "remaining_seconds": remaining,
        "state": "ringing" if remaining == 0 else "running",
        "set_by_member": timer.set_by_member,
        "set_by_profile_id": timer.set_by_profile_id,
    }


def list_timers(db: Session) -> list[dict]:
    """Every timer running or ringing, soonest first."""
    now = _now()
    rows = repository.running_timers(db)
    return [view(t, now) for t in rows if now - _as_utc(t.ends_at) <= RING_FOR]


def start_timer(
    db: Session,
    *,
    seconds: int,
    label: str | None = None,
    session_id: UUID | None = None,
    step_number: int | None = None,
    set_by_member: str | None = None,
    set_by_profile_id: UUID | None = None,
) -> CookTimer:
    seconds = int(seconds)
    if seconds < 1 or seconds > MAX_SECONDS:
        raise ValueError("a timer runs for between 1 second and 24 hours")
    if session_id is not None and repository.get_cook_session(db, session_id) is None:
        raise LookupError("Cook session not found")
    running = [t for t in list_timers(db) if t["state"] == "running"]
    if len(running) >= MAX_RUNNING:
        raise ValueError(f"{MAX_RUNNING} timers are already running; stop one first")
    label = (label or "").strip()[:80] or (f"Step {step_number}" if step_number else spoken_duration(seconds))
    now = _now()
    timer = CookTimer(
        session_id=session_id,
        label=label,
        step_number=step_number,
        duration_seconds=seconds,
        status="running",
        started_at=now,
        ends_at=now + timedelta(seconds=seconds),
        set_by_member=(set_by_member or "").strip()[:128] or None,
        set_by_profile_id=set_by_profile_id,
    )
    repository.add(db, timer)
    db.commit()
    db.refresh(timer)
    return timer


def find_timer(db: Session, *, timer_id: UUID | None = None, label: str | None = None) -> CookTimer:
    """A running or ringing timer by id, or by label ("the pasta timer").

    By label, a whole-label match wins over a partial one; more than one
    equally good match is refused rather than guessed, and with neither an id
    nor a label, the only timer there is.
    """
    if timer_id is not None:
        timer = repository.get_timer(db, timer_id)
        if timer is None or timer.status != "running":
            raise LookupError("No running timer with that id")
        return timer
    live = list_timers(db)
    if label:
        want = label.strip().lower()
        exact = [t for t in live if t["label"].lower() == want]
        partial = [t for t in live if want in t["label"].lower()]
        found = exact or partial
    else:
        found = live
    if not found:
        raise LookupError(f"No timer called {label!r} is running" if label else "No timer is running")
    if len(found) > 1:
        names = ", ".join(t["label"] for t in found)
        raise ValueError(f"More than one timer matches ({names}); say which")
    return repository.get_timer(db, found[0]["id"])


def stop_timer(db: Session, *, timer_id: UUID | None = None, label: str | None = None) -> dict:
    """Stop a timer: cancelled if it had not gone off yet, dismissed if it had."""
    timer = find_timer(db, timer_id=timer_id, label=label)
    now = _now()
    before = view(timer, now)
    timer.status = "cancelled" if before["state"] == "running" else "dismissed"
    timer.stopped_at = now
    repository.add(db, timer)
    db.commit()
    return {**before, "stopped": timer.status}


def timers_ended_between(db: Session, *, start: datetime, end: datetime) -> list[dict]:
    """Timers that went off in (start, end] and were not cancelled first — for
    the Wildstone relay's ``pantry.timer_finished`` (ADR 0026 §4)."""
    # The range in SQL keeps a pass from reading every timer ever set; the
    # exact comparison below is done on UTC-normalised values.
    rows = repository.timers_ending_in(db, start=start - timedelta(minutes=1), end=end + timedelta(minutes=1))
    out = []
    for t in rows:
        ends_at = _as_utc(t.ends_at)
        if start < ends_at <= end and (t.stopped_at is None or _as_utc(t.stopped_at) >= ends_at):
            out.append(t)
    recipes = {}
    result = []
    for t in sorted(out, key=lambda t: _as_utc(t.ends_at)):
        recipe_name = None
        if t.session_id is not None:
            if t.session_id not in recipes:
                session = repository.get_cook_session(db, t.session_id)
                recipes[t.session_id] = _recipe_name(db, session.recipe_id) if session else None
            recipe_name = recipes[t.session_id]
        result.append({**view(t, end), "recipe_name": recipe_name})
    return result


def _recipe_name(db: Session, recipe_id: UUID) -> str | None:
    from modules.planning.services.recipes import get_recipe_with_ingredients

    try:
        recipe, _ = get_recipe_with_ingredients(db, recipe_id)
    except LookupError:
        return None
    return recipe.name
