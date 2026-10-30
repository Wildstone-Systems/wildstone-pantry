"""Build Wildstone household event envelopes (ADR 0026 §4; ADR-W024 A2).

Pure: no DB, no HTTP. Every function takes plain data and returns the
envelope Keystone's intake accepts:

    {eventId, occurredAt, type, subject?, entity?, payload, correlationId?, schemaVersion}

``eventId`` is a UUIDv5 over the *source* of the event (a ledger row, a cook
record, a lot + its date), so deriving the same event twice gives the same id
and both the outbox and Keystone drop the repeat. ``source`` and
``recorded_at`` are deliberately absent: the intake stamps those itself from
the publisher token and its own clock.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

SCHEMA_VERSION = 1

# Fixed namespace so ids are stable across restarts and reinstalls.
_NAMESPACE = uuid.UUID("5f0b1f3e-8f0e-4c55-9d1a-7a0c2b6e5d11")

# Reasons on a `consumed` ledger row that mean "someone ate this".
MEAL_REASONS = frozenset({"plate", "plate_recipe"})


def event_id(kind: str, key: str) -> str:
    return str(uuid.uuid5(_NAMESPACE, f"pantryos:{kind}:{key}"))


def _iso(value: datetime) -> str:
    value = value if value.tzinfo else value.replace(tzinfo=UTC)
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _num(value: Any) -> float | None:
    if value is None:
        return None
    return float(Decimal(str(value)).normalize())


def _envelope(
    *,
    kind: str,
    key: str,
    type_: str,
    occurred_at: datetime,
    payload: dict,
    subject: str | None = None,
    entity: str | None = None,
    correlation_id: str | None = None,
) -> dict:
    env: dict[str, Any] = {
        "eventId": event_id(kind, key),
        "occurredAt": _iso(occurred_at),
        "type": type_,
        "payload": {k: v for k, v in payload.items() if v is not None},
        "schemaVersion": SCHEMA_VERSION,
    }
    if subject:
        env["subject"] = subject
    if entity:
        env["entity"] = entity[:256]
    if correlation_id:
        env["correlationId"] = correlation_id
    return env


def is_meal(row: dict) -> bool:
    """A ledger row that records someone eating — the primary row only.

    A plated recipe writes one row per ingredient but only the first carries the
    eaten amount; the rest would be the same meal told again.
    """
    if row["event_type"] != "consumed" or row.get("reason") not in MEAL_REASONS:
        return False
    return bool((row.get("payload") or {}).get("quantity"))


def meal_logged(row: dict, *, member: str | None) -> dict:
    """``pantry.meal_logged`` from a plate row.

    Carries what and when, not nutrition: calories stay in PantryOS unless a
    person asks for them there.
    """
    payload = row.get("payload") or {}
    return _envelope(
        kind="meal",
        key=str(row["event_id"]),
        type_="pantry.meal_logged",
        occurred_at=row["occurred_at"],
        subject=member,
        entity=row["item_name"],
        payload={
            "food": row["item_name"],
            "meal_type": payload.get("meal_type"),
            "quantity": _num(payload.get("quantity")),
            "unit": payload.get("unit"),
            "profile_id": str(payload.get("profile_id") or row.get("actor") or "") or None,
        },
    )


# Reasons on a `purchased` row that come from importing one receipt or order;
# its `note` names that receipt ("purchase:<id>", or the vendor's order id).
RECEIPT_REASONS = frozenset({"purchase_import", "imap_purchase_import"})


def purchase_key(row: dict) -> str:
    """Which purchase a `purchased` row belongs to.

    Rows imported from one receipt share the receipt's reference; anything else
    (a single item added by hand, a scan) is its own purchase. The ledger has no
    transaction id to group on, and a relay pass is not a purchase.
    """
    if row.get("reason") in RECEIPT_REASONS and row.get("note"):
        return f"receipt:{row['reason']}:{row['note']}"
    return f"row:{row['event_id']}"


def purchase_recorded(key: str, rows: list[dict]) -> dict:
    """One ``pantry.purchase_recorded`` per purchase — a whole receipt, or one
    hand-added item. Keyed on the purchase, so it has one id however it is read."""
    last = rows[-1]
    items = [
        {"item": r["item_name"][:80], "quantity": _num(r["quantity"]), "unit": r["unit"]} for r in rows[:25]
    ]
    return _envelope(
        kind="purchase",
        key=key,
        type_="pantry.purchase_recorded",
        occurred_at=last["occurred_at"],
        payload={
            "from_receipt": key.startswith("receipt:") or None,
            "item_count": len(rows),
            "items": items,
            "truncated": len(rows) > len(items) or None,
        },
    )


def recipe_cooked(cook: dict) -> dict:
    servings = cook.get("servings_made")
    return _envelope(
        kind="cook",
        key=str(cook["cook_event_id"]),
        type_="pantry.recipe_cooked",
        occurred_at=cook["created_at"],
        entity=cook["recipe_name"],
        payload={
            "recipe": cook["recipe_name"],
            "recipe_id": str(cook["recipe_id"]),
            "servings_made": _num(servings) if servings is not None else None,
            "batches": cook.get("servings") if servings is None else None,
            "leftover_servings": cook.get("leftover_servings") or None,
        },
    )


def stock_low(item: dict, *, at_seq: int, crossing: int, now: datetime) -> dict:
    """Keyed on the ledger position the crossing was seen at AND the crossing's
    count. The position keeps a crossing after a reconnect (when the count
    starts again) from reusing an old id; the count separates crossings with no
    stock change between them, e.g. a threshold edited down and back up."""
    return _envelope(
        kind="stock_low",
        key=f"{item['item_id']}:{at_seq}:{crossing}",
        type_="pantry.stock_low",
        occurred_at=now,
        entity=item["name"],
        payload={
            "item": item["name"],
            "item_id": str(item["item_id"]),
            "on_hand": _num(item["on_hand"]),
            "threshold": _num(item["threshold"]),
            "unit": item.get("unit"),
        },
    )


def item_expiring(lot: dict, *, now: datetime) -> dict:
    expires: date = lot["expiration_date"]
    return _envelope(
        kind="expiring",
        key=f"{lot['lot_id']}:{expires.isoformat()}",
        type_="pantry.item_expiring",
        occurred_at=now,
        entity=lot["item_name"],
        payload={
            "item": lot["item_name"],
            "item_id": str(lot["item_id"]),
            "expires_on": expires.isoformat(),
            "days_left": lot["days_until_expiration"],
            "quantity": _num(lot.get("quantity_remaining")),
            "unit": lot.get("unit"),
        },
    )


def item_spoiled(row: dict) -> dict:
    """``pantry.item_spoiled`` from a ``spoiled`` ledger row: something went
    off and was thrown out. Keyed on the ledger row."""
    return _envelope(
        kind="spoiled",
        key=str(row["event_id"]),
        type_="pantry.item_spoiled",
        occurred_at=row["occurred_at"],
        entity=row["item_name"],
        payload={
            "item": row["item_name"][:80],
            "quantity": _num(row["quantity"]),
            "unit": row["unit"],
        },
    )


def shopping_list_changed(*, before: str, after: str, item_count: int, changed_at: datetime) -> dict:
    """``pantry.shopping_list_changed``: the grocery list settled into a new
    state after a burst of edits. Only a count — what is on it stays in
    PantryOS, one ``pantry.list`` query away. Keyed on the change from one
    state to the next, so each change is one event however often it is read."""
    return _envelope(
        kind="shopping_list",
        # The time is part of the key: the list can go A -> B -> A -> B, and
        # the second A -> B is news too.
        key=f"{before}->{after}@{_iso(changed_at)}",
        type_="pantry.shopping_list_changed",
        occurred_at=changed_at,
        payload={"item_count": item_count},
    )


def timer_finished(timer: dict, *, member: str | None) -> dict:
    """A kitchen timer went off (ADR 0026 §6.1). Keyed on the timer, so one
    timer is one event however many passes see it. The subject is whoever
    set it, when that is known, so a routine can tell THEM."""
    return _envelope(
        kind="timer",
        subject=member,
        key=str(timer["id"]),
        type_="pantry.timer_finished",
        occurred_at=timer["ends_at"],
        entity=timer["label"],
        payload={
            "label": timer["label"],
            "duration_seconds": timer["duration_seconds"],
            "recipe": timer.get("recipe_name"),
            "step_number": timer.get("step_number"),
        },
    )


def connection_tested(*, now: datetime) -> dict:
    return _envelope(
        kind="test",
        key=_iso(now),
        type_="pantry.connection_tested",
        occurred_at=now,
        payload={"message": "PantryOS can reach Wildstone."},
    )
