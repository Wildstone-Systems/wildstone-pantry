"""Learned shelf life — infer an item's best-by offset from its own history.

The premise: a ``purchased`` event records both when an item was checked in and
the best-by date the operator typed at the time, on the same row. The gap
between them is one observation of "how long this item lasts from purchase".
Enough consistent observations and we can offer the date instead of asking.

Two things make this defensible rather than a guess:

**We read the event log, not the projection.** ``inv_lot.created_at`` looks like
the obvious source and is a trap — lots are a rebuildable projection (ADR 0002),
so that column is when the projection was last written, not when anything was
bought. A rebuild sets every row to the same instant and would silently teach us
that the entire catalogue expires the day it is purchased. ``InventoryEvent`` is
append-only and never rewritten, so ``occurred_at`` is the real thing.

**Silence is the default.** With no history we return ``None`` and the field
stays blank, exactly as today. A suggestion only appears once the observations
both exist and agree — see ``_agrees`` for what "agree" means and why the
tolerance is relative.

Scoped to one catalogue item, never pooled across a family. That is not a
limitation, it is the mechanism: raw chicken and frozen chicken are separate
items, so they learn separate shelf lives without anyone having to classify
them. Pooling them would average three days against six months and be right for
neither.

The gate also makes the feature self-selecting in a way worth stating plainly.
Things you eat well before they turn — a can of peanuts, a bag of rice — never
accumulate observations, because nobody bothers typing a best-by for them. So
they never get a suggestion, which is correct: they didn't need one. The items
that do reach the threshold are exactly the ones where the operator has been
recording dates, which is exactly where the date matters.

Nothing is stored. This is derived on read, so it is always current and there is
no denormalised copy to fall out of date. ``Item.default_expiration_days``
remains the coarse fallback for items with no history of their own.
"""

from __future__ import annotations

from dataclasses import dataclass
from statistics import median
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from modules.inventory.models.event import InventoryEvent

# How many dated purchases before we will say anything. Two is not enough: the
# median of two values is their mean, so a single typo ("2027" for "2026")
# corrupts the estimate outright with nothing to outvote it. Three is the
# smallest n where one bad observation can be seen for what it is.
MIN_OBSERVATIONS = 3

# An observation must be a positive number of days — a best-by already in the
# past at check-in is a clearance sticker or a typo, not a shelf life — and
# within a sane ceiling.
MAX_PLAUSIBLE_DAYS = 1825  # 5 years

# How closely the observations must agree, as a fraction of the median.
#
# Relative rather than absolute, deliberately. A tight absolute band would be
# far too strict for shelf-stable goods (a can dated 400 vs 550 days out is
# perfectly consistent — the printed date tracks manufacture, and warehouse time
# varies) and far too loose for fresh (chicken at 3 vs 17 days is not a shelf
# life, it is two different products). Scaling the tolerance to the magnitude
# gets both right, and it matches the consequence of being wrong: seventy days
# off on a can of tomatoes costs nothing, four days off on chicken matters.
MAX_RELATIVE_SPREAD = 0.5

# ...with an absolute floor, so very short shelf lives are not held to an
# impossible standard. 2, 3, 4 days spans 100% of its median but is obviously
# a consistent "about 3 days".
ABSOLUTE_SPREAD_ALLOWANCE_DAYS = 3


# What a cooked dish gets when nothing better is known.
#
# Leftovers are the one case where a convention beats learning. They are never
# *purchased*, so they accumulate no observations and would sit in inventory
# with no date forever — invisible to the expiring-soon list, which is exactly
# where they need to appear if they are going to be eaten before they turn.
# Four days is the common food-safety guidance for cooked leftovers refrigerated
# promptly, and it is deliberately a floor rather than a promise: it surfaces
# the dish while it is still good rather than declaring when it goes bad.
LEFTOVER_SHELF_LIFE_DAYS = 4


@dataclass(frozen=True)
class ShelfLifeHint:
    """A learned best-by offset, with the evidence that justifies it.

    ``observations`` and ``spread_days`` exist so the UI can show its working.
    A prefilled date with no visible basis is a mystery number; the whole point
    is that the operator can see *why* it says 14 days and overrule it.
    """

    days: int
    observations: int
    spread_days: int
    source: str = "learned"

    def as_dict(self) -> dict:
        return {
            "days": self.days,
            "observations": self.observations,
            "spread_days": self.spread_days,
            "source": self.source,
        }


def _observations(db: Session, item_id: UUID) -> list[int]:
    """Days between check-in and the best-by, resolved per lot, oldest first.

    Observations are keyed by lot rather than by event, because the best-by for
    a lot can be filled in or corrected *after* the fact: ``set_item_expiration``
    appends a ``relabeled`` event rather than rewriting history (the log is
    append-only). So a purchase checked in with the field left blank becomes a
    usable observation the day you look up what you threw away and record it —
    the estimate improves retroactively, which is the point.

    That also means the naive "every event with an expiration_date" query is
    wrong twice over: it would count a relabelled lot twice, and it would keep
    counting the stale value the relabel exists to correct. For each lot we take
    the purchase date from the ``purchased`` event and the *latest* expiration
    anyone has asserted for it.

    A purchase with no expiry — then or since — is not an observation at all.
    The operator skipped the field, which tells us nothing either way.
    """
    rows = db.execute(
        select(
            InventoryEvent.lot_id,
            InventoryEvent.event_type,
            InventoryEvent.occurred_at,
            InventoryEvent.expiration_date,
        )
        .where(
            InventoryEvent.item_id == item_id,
            InventoryEvent.event_type.in_(("purchased", "relabeled")),
        )
        .order_by(InventoryEvent.seq)
    ).all()

    # lot_id -> [purchase date, latest asserted expiry]. Ordering by seq means a
    # later relabel simply overwrites the expiry as we walk forward.
    purchased_on: dict[UUID, object] = {}
    expires_on: dict[UUID, object] = {}
    # Lot-less purchases (older rows, imports) can't be corrected or deduplicated,
    # so they stand alone on whatever they were checked in with.
    orphans: list[int] = []

    for lot_id, event_type, occurred_at, expiration_date in rows:
        if occurred_at is None:
            continue
        if lot_id is None:
            if event_type == "purchased" and expiration_date is not None:
                orphans.append((expiration_date - occurred_at.date()).days)
            continue
        if event_type == "purchased":
            purchased_on.setdefault(lot_id, occurred_at.date())
        if expiration_date is not None:
            expires_on[lot_id] = expiration_date

    days = list(orphans)
    for lot_id, bought in purchased_on.items():
        expiry = expires_on.get(lot_id)
        if expiry is not None:
            days.append((expiry - bought).days)

    return [d for d in days if 0 < d <= MAX_PLAUSIBLE_DAYS]


def _agrees(days: list[int]) -> bool:
    """Do these observations agree closely enough to stand behind one number?"""
    spread = max(days) - min(days)
    if spread <= ABSOLUTE_SPREAD_ALLOWANCE_DAYS:
        return True
    mid = median(days)
    return mid > 0 and spread / mid <= MAX_RELATIVE_SPREAD


def learned_shelf_life(db: Session, item_id: UUID) -> ShelfLifeHint | None:
    """The item's learned shelf life, or ``None`` to stay silent.

    ``None`` covers every uncertain case — too few observations, or observations
    that disagree — and callers must treat it as "leave the field blank", never
    as a zero.
    """
    days = _observations(db, item_id)
    if len(days) < MIN_OBSERVATIONS:
        return None
    if not _agrees(days):
        return None

    # Median, not mean: one mistyped year should not drag the estimate, and with
    # n>=3 the median simply steps over it.
    return ShelfLifeHint(
        days=int(round(median(days))),
        observations=len(days),
        spread_days=max(days) - min(days),
    )
