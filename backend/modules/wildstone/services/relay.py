"""The event relay: PantryOS facts → Wildstone outbox (ADR 0026 §4).

One pass reads everything new since the last pass and writes the envelopes to
``wildstone_outbox`` in the SAME transaction that moves the cursors forward.
So a crash either loses the whole pass (and it is re-read next time) or keeps
all of it — never "cursor moved, events gone".

Sources, all read through other modules' published services:

* the inventory ledger (meals, purchases) — by sequence number, re-reading
  the last few minutes below the cursor for rows that committed late (held,
  like new rows, while their receipt is still arriving);
* recipe cook records — by time, de-duplicated on the cook id;
* restock thresholds — ``stock_low`` fires on the crossing only;
* expiring lots — checked each pass, one event per lot and date;
* the grocery list — one event per settled change, with only a count;
* kitchen timers — each one that went off, once (ADR 0026 §6.1).
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

from sqlalchemy.orm import Session

from modules.inventory.services.feed import events_after, late_events
from modules.planning.services.cook_timers import timers_ended_between
from modules.planning.services.insights import list_expiring_items
from modules.planning.services.recipes import cook_events_since
from modules.shopping.services.shopping_list import items_below_threshold, list_state
from modules.wildstone import repository
from modules.wildstone.domain import envelopes
from modules.wildstone.models import WildstoneOutbox, WildstoneStockFlag
from modules.wildstone.services.connection import get_state, is_configured
from modules.wildstone.services.members import member_for

LEDGER_PAGE = 500
SETTLE = timedelta(seconds=5)
# How long a ledger row may take to commit and still be found after the cursor
# has moved past it (a sequence number is taken before commit). The outbox
# keeps delivered ids for far longer, so a re-read row is never sent twice.
LATE_WINDOW = timedelta(minutes=15)
# A receipt whose newest line is younger than this is still being imported.
RECEIPT_QUIET = timedelta(seconds=60)
# A burst of grocery-list edits is one change: report it once this long has
# passed since the last edit.
LIST_QUIET = timedelta(seconds=60)
# Cook records are read with a little overlap; the outbox drops the repeats.
COOK_OVERLAP = timedelta(seconds=2)
EXPIRING_WITHIN_DAYS = 3
# How far back each pass looks for timers that went off. Wider than a pass so
# a slow pass misses none; the timer-keyed event id drops the repeats. A timer
# that went off longer ago than this is not news any more.
TIMER_LOOKBACK = timedelta(minutes=10)


def _as_utc(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def _consumable(rows: list[dict], *, now: datetime, quiet: timedelta, page_full: bool) -> list[dict]:
    """The leading rows safe to turn into events now.

    A receipt is imported line by line, each line its own commit, so a pass can
    land in the middle of one. Stop the cursor at the first line of any receipt
    that is still arriving (a line newer than ``quiet``), or that runs into the
    end of a full page, so the receipt is read whole next time instead of being
    reported as two purchases. A receipt that fills a page on its own is read
    anyway — holding it would stall the relay for good.
    """
    first_row: dict[str, int] = {}
    latest: dict[str, datetime] = {}
    for index, row in enumerate(rows):
        if row["event_type"] != "purchased":
            continue
        key = envelopes.purchase_key(row)
        if not key.startswith("receipt:"):
            continue
        first_row.setdefault(key, index)
        latest[key] = max(latest.get(key, row["recorded_at"]), row["recorded_at"])

    cut = len(rows)
    last_key = envelopes.purchase_key(rows[-1]) if rows and rows[-1]["event_type"] == "purchased" else None
    for key, start in first_row.items():
        still_arriving = quiet and now - latest[key] < quiet
        runs_off_page = page_full and key == last_key and start > 0
        if still_arriving or runs_off_page:
            cut = min(cut, start)
    return rows[:cut]


def _receipt_key(row: dict) -> str | None:
    if row["event_type"] != "purchased":
        return None
    key = envelopes.purchase_key(row)
    return key if key.startswith("receipt:") else None


def _late_rows_ready(
    late: list[dict], fetched: list[dict], *, held: list[dict], now: datetime, quiet: timedelta
) -> list[dict]:
    """The late rows safe to turn into events now.

    A late receipt line is held — it is re-read next pass, being recent —
    while its receipt is still arriving: a line of it is among the new rows
    this pass holds back, or its newest line, late or new, is younger than
    ``quiet``. Otherwise the receipt would go out partial, and the rest of it,
    sharing the receipt's event id, would be dropped as a repeat.
    """
    held_keys = {key for row in held if (key := _receipt_key(row))}
    latest: dict[str, datetime] = {}
    for row in [*late, *fetched]:
        if key := _receipt_key(row):
            latest[key] = max(latest.get(key, row["recorded_at"]), row["recorded_at"])
    ready = []
    for row in late:
        key = _receipt_key(row)
        if key and (key in held_keys or (quiet and now - latest[key] < quiet)):
            continue
        ready.append(row)
    return ready


def _ledger_envelopes(db: Session, rows: list[dict]) -> list[tuple[datetime, dict]]:
    """Meals and purchases in ledger order. A receipt's lines become ONE
    purchase, placed where its first line is — so a purchase followed by a
    meal is still delivered purchase first."""
    order: list[tuple[datetime, dict | str]] = []
    purchases: dict[str, list[dict]] = {}
    for row in rows:
        if envelopes.is_meal(row):
            profile_id = (row.get("payload") or {}).get("profile_id") or row.get("actor")
            order.append((row["recorded_at"], envelopes.meal_logged(row, member=member_for(db, profile_id))))
        elif row["event_type"] == "spoiled":
            order.append((row["recorded_at"], envelopes.item_spoiled(row)))
        elif row["event_type"] == "purchased":
            key = envelopes.purchase_key(row)
            if key not in purchases:
                order.append((row["recorded_at"], key))
            purchases.setdefault(key, []).append(row)
    return [
        (at, envelopes.purchase_recorded(e, purchases[e]) if isinstance(e, str) else e) for at, e in order
    ]


def _list_envelopes(db: Session, state, now: datetime, quiet: timedelta) -> list[tuple[datetime, dict]]:
    """``pantry.shopping_list_changed`` once the grocery list has settled into
    a state other than the one last reported — one event per burst of edits.
    A relay with no reported state yet takes the list as it stands as its
    starting picture: connecting is not news."""
    current = list_state(db)
    fingerprint = current["fingerprint"]
    if state.list_fingerprint is None:
        state.list_fingerprint = fingerprint
        return []
    if fingerprint == state.list_fingerprint:
        return []
    if current["changed_at"] is None:
        # Nothing left in the list at all — its last entry was deleted, and
        # a deletion leaves no timestamp behind. Nothing more can arrive to
        # wait for: report it now.
        changed_at = now
    else:
        changed_at = _as_utc(current["changed_at"])
        if quiet and now - changed_at < quiet:
            return []  # still being edited: wait for the burst to end
    env = envelopes.shopping_list_changed(
        before=state.list_fingerprint, after=fingerprint, item_count=current["pending"], changed_at=changed_at
    )
    state.list_fingerprint = fingerprint
    return [(changed_at, env)]


def _stock_envelopes(db: Session, now: datetime, at_seq: int) -> list[dict]:
    below = {item["item_id"]: item for item in items_below_threshold(db)}
    flags = repository.stock_flags(db)
    out: list[dict] = []
    for item_id, item in below.items():
        flag = flags.get(item_id)
        if flag is not None and flag.low:
            continue  # still low: already told
        if flag is None:
            flag = WildstoneStockFlag(item_id=item_id, low=True, crossings=1, updated_at=now)
        else:
            flag.low, flag.crossings, flag.updated_at = True, flag.crossings + 1, now
        repository.save_stock_flag(db, flag)
        out.append(envelopes.stock_low(item, at_seq=at_seq, crossing=flag.crossings, now=now))
    for item_id, flag in flags.items():
        if flag.low and item_id not in below:
            flag.low, flag.updated_at = False, now  # recovered: may fire again later
            repository.save_stock_flag(db, flag)
    return out


def _expiring_envelopes(db: Session, now: datetime) -> list[dict]:
    lots = list_expiring_items(db, location_id=None, within_days=EXPIRING_WITHIN_DAYS)
    # Already-expired food is the expiring list's business, not news.
    return [envelopes.item_expiring(lot, now=now) for lot in lots if lot["days_until_expiration"] >= 0]


def _in_time_order(items: list[tuple[datetime, dict]]) -> list[dict]:
    """One pass gathers several sources — ledger, cooks, stock, expiry,
    timers — and the outbox delivers in the order rows are added. Merge them
    by when PantryOS recorded each fact (a ledger row's recorded_at, a cook's
    created_at, a timer's end, "now" for what a pass finds), so a recipe
    cooked before a meal is sent before it. The sort is stable: facts from
    the same moment keep the order their source gave them."""
    return [env for _, env in sorted(items, key=lambda item: _as_utc(item[0]))]


def _enqueue(db: Session, items: list[dict], now: datetime) -> int:
    if not items:
        return 0
    ids = [env["eventId"] for env in items]
    known = repository.known_event_ids(db, ids)
    added = 0
    for env in items:
        if env["eventId"] in known:
            continue
        known.add(env["eventId"])
        repository.add_outbox_row(
            db,
            WildstoneOutbox(
                event_id=env["eventId"],
                event_type=env["type"],
                envelope=env,
                status="pending",
                attempts=0,
                next_attempt_at=now,
                created_at=now,
            ),
        )
        added += 1
    return added


def run_pass(
    db: Session,
    *,
    now: datetime | None = None,
    settle: timedelta = SETTLE,
    receipt_quiet: timedelta = RECEIPT_QUIET,
    list_quiet: timedelta = LIST_QUIET,
    today: date | None = None,
) -> int:
    """Read what's new and queue it. Returns how many events were queued."""
    # Locked for the whole pass: a target change (which resets the relay)
    # waits for this pass to commit, then clears what it queued — events
    # derived for one Keystone are never sent with the next one's token.
    state = get_state(db, for_update=True)
    if state is None or not is_configured(db):
        return 0
    now = now or datetime.now(UTC)
    today = today or now.date()
    # (when PantryOS recorded it, envelope) — merged into time order below.
    queued: list[tuple[datetime, dict]] = []

    # Rows that committed after the cursor passed their number first — the
    # outbox drops the ones already queued.
    late: list[dict] = []
    if state.ledger_seq > state.start_seq:
        late = late_events(
            db, through_seq=state.ledger_seq, floor_seq=state.start_seq, recorded_since=now - LATE_WINDOW
        )

    fetched = events_after(
        db, after_seq=state.ledger_seq, limit=LEDGER_PAGE, settled_before=(now - settle) if settle else None
    )
    rows = _consumable(fetched, now=now, quiet=receipt_quiet, page_full=len(fetched) == LEDGER_PAGE)
    # Late and new rows go out as ONE set: a receipt whose lines fall on both
    # sides of the cursor is one purchase, and a late line of a receipt that
    # is still arriving waits with the rest of it (#81).
    ready_late = _late_rows_ready(late, fetched, held=fetched[len(rows):], now=now, quiet=receipt_quiet)
    by_seq = {row["seq"]: row for row in [*ready_late, *rows]}
    queued.extend(_ledger_envelopes(db, [by_seq[seq] for seq in sorted(by_seq)]))
    if rows:
        state.ledger_seq = rows[-1]["seq"]

    since = _as_utc(state.cook_cursor) - COOK_OVERLAP if state.cook_cursor else None
    for cook in cook_events_since(db, since=since):
        created = _as_utc(cook["created_at"])
        queued.append((created, envelopes.recipe_cooked(cook)))
        if state.cook_cursor is None or created > _as_utc(state.cook_cursor):
            state.cook_cursor = created

    queued.extend((now, env) for env in _stock_envelopes(db, now, state.ledger_seq))
    queued.extend(_list_envelopes(db, state, now, list_quiet))

    # Checked every pass (a lot bought today can expire in two days); the
    # lot + date event id is what makes it once per lot.
    queued.extend((now, env) for env in _expiring_envelopes(db, now))
    state.expiry_scan_on = today

    for timer in timers_ended_between(db, start=now - TIMER_LOOKBACK, end=now):
        # Set by an agent: the member it acted for. Set on the tablet: the
        # member linked to the profile picked there. Otherwise nobody.
        member = timer.get("set_by_member") or member_for(db, timer.get("set_by_profile_id"))
        queued.append((timer["ends_at"], envelopes.timer_finished(timer, member=member)))

    added = _enqueue(db, _in_time_order(queued), now)
    state.updated_at = now
    repository.add_relay_state(db, state)
    db.commit()
    return added
