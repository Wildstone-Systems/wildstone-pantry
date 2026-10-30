"""Persistence for the Wildstone integration (the DB-access layer).

The services decide; this module is the only place in ``modules.wildstone``
that touches the ORM (AGENTS.md rule 1: routes → services → repository →
ORM). Functions stage changes on the session; committing stays with the
service that owns the transaction, so a relay pass still writes its events
and moves its cursors in one commit.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from modules.wildstone.models import WildstoneMemberLink, WildstoneOutbox, WildstoneRelayState, WildstoneStockFlag

# --- relay state -------------------------------------------------------------


def get_relay_state(db: Session, name: str, *, for_update: bool = False) -> WildstoneRelayState | None:
    """The relay's cursors. ``for_update`` row-locks them (Postgres) for the
    rest of the transaction — a relay pass holds it, so a reset waits for the
    pass to commit rather than racing it."""
    if not for_update:
        return db.get(WildstoneRelayState, name)
    return db.execute(
        select(WildstoneRelayState).where(WildstoneRelayState.name == name).with_for_update()
    ).scalar_one_or_none()


def add_relay_state(db: Session, state: WildstoneRelayState) -> None:
    db.add(state)


def reset_relay(db: Session, *, forget_history: bool = False) -> None:
    """Forget undelivered events, the stock picture and the cursors. Delivered
    rows stay as history for the status screen — unless ``forget_history``:
    a different Keystone is a different household, and ids already sent to
    the old one must not stop the same news reaching the new one."""
    # The state row FIRST: a relay pass holds it locked until it commits, so
    # this waits for that pass — and the outbox is cleared only afterwards,
    # including anything the pass queued for the old target.
    db.execute(delete(WildstoneRelayState))
    db.execute(delete(WildstoneStockFlag))
    if forget_history:
        db.execute(delete(WildstoneOutbox))
    else:
        db.execute(delete(WildstoneOutbox).where(WildstoneOutbox.status == "pending"))


# --- stock flags ---------------------------------------------------------------


def stock_flags(db: Session) -> dict[UUID, WildstoneStockFlag]:
    return {flag.item_id: flag for flag in db.execute(select(WildstoneStockFlag)).scalars()}


def save_stock_flag(db: Session, flag: WildstoneStockFlag) -> None:
    db.add(flag)


def merge_stock_flag(db: Session, flag: WildstoneStockFlag) -> None:
    db.merge(flag)


# --- outbox ----------------------------------------------------------------------


def known_event_ids(db: Session, event_ids: list[str]) -> set[str]:
    if not event_ids:
        return set()
    return set(db.execute(select(WildstoneOutbox.event_id).where(WildstoneOutbox.event_id.in_(event_ids))).scalars())


def add_outbox_row(db: Session, row: WildstoneOutbox) -> None:
    db.add(row)


def pending_outbox(db: Session, *, limit: int) -> list[WildstoneOutbox]:
    """The oldest undelivered rows, in the order they must be sent."""
    return list(
        db.execute(
            select(WildstoneOutbox)
            .where(WildstoneOutbox.status == "pending")
            .order_by(WildstoneOutbox.id.asc())
            .limit(limit)
        ).scalars()
    )


def save_outbox_row(db: Session, row: WildstoneOutbox) -> None:
    db.add(row)


def prune_delivered(db: Session, *, before: datetime) -> None:
    db.execute(
        delete(WildstoneOutbox).where(WildstoneOutbox.status == "delivered", WildstoneOutbox.delivered_at < before)
    )


def outbox_summary(db: Session) -> dict:
    """Counts by status, the last delivery, and the newest error still pending."""
    counts = dict(db.execute(select(WildstoneOutbox.status, func.count()).group_by(WildstoneOutbox.status)).all())
    last_delivered = db.execute(select(func.max(WildstoneOutbox.delivered_at))).scalar()
    last_error = db.execute(
        select(WildstoneOutbox.last_error)
        .where(WildstoneOutbox.last_error.is_not(None), WildstoneOutbox.status != "delivered")
        .order_by(WildstoneOutbox.id.desc())
        .limit(1)
    ).scalar()
    return {"counts": counts, "last_delivered_at": last_delivered, "last_error": last_error}


# --- member links -------------------------------------------------------------


def get_member_link(db: Session, profile_id: UUID) -> WildstoneMemberLink | None:
    return db.get(WildstoneMemberLink, profile_id)


def member_links(db: Session) -> dict[UUID, str]:
    return {row.profile_id: row.member for row in db.execute(select(WildstoneMemberLink)).scalars()}


def set_member_link(db: Session, profile_id: UUID, member: str | None) -> None:
    """Link a profile to a member, relink it, or (member None) unlink it."""
    existing = db.get(WildstoneMemberLink, profile_id)
    if member is None:
        if existing is not None:
            db.delete(existing)
    elif existing is None:
        db.add(WildstoneMemberLink(profile_id=profile_id, member=member))
    else:
        existing.member = member
