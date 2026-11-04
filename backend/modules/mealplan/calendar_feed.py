"""Read-only iCal feed of the meal plan (#33).

Subscribe to it in a phone or home-lab calendar. Each planned meal is an
all-day event on its day — meal plans have no clock time, and an all-day event
can't drift across a time zone. The feed covers two weeks back to two months
ahead.

Calendar apps can't send headers, so the URL carries a secret. Only its hash
is stored; making a new link replaces the old one; turning it off removes it.
The feed can only be read — it never changes the plan.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import UTC, date, datetime, timedelta

from sqlalchemy.orm import Session

from app.services.profiles import get_profile
from modules.mealplan import repository
from modules.mealplan.services import list_week

FEED_PATH = "/api/meal-plan/calendar.ics"
PAST_DAYS = 14
AHEAD_DAYS = 60
_PREFIX = "mpcal_"


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def status(db: Session) -> dict:
    feed = repository.calendar_feed(db)
    return {"enabled": feed is not None, "created_at": feed.created_at if feed else None}


def create_link(db: Session) -> dict:
    """A new secret link. Shown once: only its hash is kept. Replaces any old link."""
    token = _PREFIX + secrets.token_urlsafe(32)
    feed = repository.replace_calendar_feed(db, _hash(token))
    db.commit()
    db.refresh(feed)
    return {"path": f"{FEED_PATH}?token={token}", "created_at": feed.created_at}


def turn_off(db: Session) -> None:
    repository.delete_calendar_feed(db)
    db.commit()


def token_is_valid(db: Session, token: str | None) -> bool:
    if not token or not token.startswith(_PREFIX):
        return False
    feed = repository.calendar_feed_by_hash(db, _hash(token))
    # The lookup is by hash; compare again in constant time for good measure.
    return feed is not None and hmac.compare_digest(feed.token_hash, _hash(token))


# --- iCalendar (RFC 5545) --------------------------------------------------------


def _escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\r\n", "\\n").replace("\n", "\\n")


def _fold(line: str) -> str:
    """Lines longer than 75 octets continue on the next line after a space."""
    raw = line.encode("utf-8")
    if len(raw) <= 75:
        return line
    parts, chunk = [], b""
    for char in line:
        encoded = char.encode("utf-8")
        if len(chunk) + len(encoded) > (75 if not parts else 74):
            parts.append(chunk.decode("utf-8"))
            chunk = b""
        chunk += encoded
    parts.append(chunk.decode("utf-8"))
    return "\r\n ".join(parts)


def _stamp(value: datetime) -> str:
    value = value if value.tzinfo else value.replace(tzinfo=UTC)
    return value.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")


def render(db: Session, *, today: date | None = None) -> str:
    today = today or date.today()
    # list_week caps one read at 42 days; read the window in chunks.
    entries = []
    cursor = today - timedelta(days=PAST_DAYS)
    end = today + timedelta(days=AHEAD_DAYS)
    while cursor < end:
        days = min(42, (end - cursor).days)
        entries.extend(list_week(db, start=cursor, days=days)["entries"])
        cursor += timedelta(days=days)

    names: dict = {}
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//PantryOS//Meal plan//EN",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        "X-WR-CALNAME:Meal plan",
    ]
    for entry in entries:
        if entry["status"] == "skipped":
            continue
        who = None
        if entry["profile_id"]:
            if entry["profile_id"] not in names:
                profile = get_profile(db, entry["profile_id"])
                names[entry["profile_id"]] = profile.display_name if profile else None
            who = names[entry["profile_id"]]
        summary = f"{entry['meal_type'].capitalize()}: {entry['meal']}" + (f" ({who})" if who else "")
        details = [f"Serves {entry['servings']}"]
        if entry["status"] == "cooked":
            details.append("Cooked")
        if entry["note"]:
            details.append(entry["note"])
        day: date = entry["plan_date"]
        lines += [
            "BEGIN:VEVENT",
            f"UID:{entry['id']}@pantryos",
            f"DTSTAMP:{_stamp(entry['updated_at'])}",
            f"LAST-MODIFIED:{_stamp(entry['updated_at'])}",
            f"DTSTART;VALUE=DATE:{day.strftime('%Y%m%d')}",
            f"DTEND;VALUE=DATE:{(day + timedelta(days=1)).strftime('%Y%m%d')}",
            f"SUMMARY:{_escape(summary)}",
            f"DESCRIPTION:{_escape(' · '.join(details))}",
            "TRANSP:TRANSPARENT",
            "END:VEVENT",
        ]
    lines.append("END:VCALENDAR")
    return "\r\n".join(_fold(line) for line in lines) + "\r\n"
