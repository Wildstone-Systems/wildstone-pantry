"""Shopping-list persistence: the repeat-safe add (ADR 0026 §2) and the
restock-threshold reads the low-stock views and Wildstone use."""

from __future__ import annotations

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.models import Item, ShoppingListItem

# Any fixed numbers: each names one lock.
_ADD_ONCE_LOCK = 26_0002
_AUTO_ENTRIES_LOCK = 26_0003


def lock_repeat_safe_adds(db: Session) -> None:
    """Serialize repeat-safe adds until this transaction ends.

    Two identical adds at once would each find nothing pending and both
    insert. A Postgres transaction-scoped advisory lock makes the second wait
    for the first to commit, so it then finds the first one's entry. SQLite
    (tests, single-user installs) already allows one writer at a time.
    """
    if db.get_bind().dialect.name == "postgresql":
        db.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": _ADD_ONCE_LOCK})


def lock_auto_entries(db: Session) -> None:
    """Serialize automatic-entry reconciliation until this transaction ends.

    A list read and a Wildstone relay pass reconciling at once would each
    find a low item missing and both add it. Same lock shape as
    :func:`lock_repeat_safe_adds`: the second waits, then sees the first's
    entry (Postgres READ COMMITTED reads fresh rows per statement).
    """
    if db.get_bind().dialect.name == "postgresql":
        db.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": _AUTO_ENTRIES_LOCK})


def pending_by_name(db: Session, name: str) -> ShoppingListItem | None:
    wanted = name.strip().lower()
    return (
        db.execute(
            select(ShoppingListItem).where(
                ShoppingListItem.status == "pending",
                func.lower(ShoppingListItem.name) == wanted,
            )
        )
        .scalars()
        .first()
    )


def items_with_restock_threshold(db: Session) -> list[Item]:
    return list(db.execute(select(Item).where(Item.restock_threshold.is_not(None))).scalars().all())


def pending_fingerprint_rows(db: Session) -> list[tuple]:
    """(id, name, quantity, unit, updated_at) of every pending entry, by id."""
    return list(
        db.execute(
            select(
                ShoppingListItem.id,
                ShoppingListItem.name,
                ShoppingListItem.quantity,
                ShoppingListItem.unit,
                ShoppingListItem.updated_at,
            )
            .where(ShoppingListItem.status == "pending")
            .order_by(ShoppingListItem.id)
        ).all()
    )


def latest_list_change(db: Session):
    """When any entry, pending or not, last changed (None for an empty table)."""
    return db.execute(select(func.max(ShoppingListItem.updated_at))).scalar()
