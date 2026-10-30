"""Learned shelf life (ADR: see modules/catalog/services/shelf_life.py).

The behaviour that matters most here is the *refusal* to answer. A wrong
prefilled best-by is worse than a blank field — it looks authoritative and
nobody re-checks it — so most of these tests pin cases where the estimator
must stay silent.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone

import pytest

from app.db import SessionLocal
from app.models.item import Item
from modules.catalog.services.shelf_life import MIN_OBSERVATIONS, learned_shelf_life
from modules.inventory.models.event import InventoryEvent


@pytest.fixture()
def db_session():
    """A plain ORM session — these exercise the service directly, not over HTTP."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@pytest.fixture()
def item(db_session):
    row = Item(name=f"Shelf Life Test {uuid.uuid4().hex[:8]}")
    db_session.add(row)
    db_session.commit()
    return row


def _purchase(db, item, *, days_ago: int, shelf_life: int | None, lot_id=None):
    """A check-in `days_ago`, optionally with a best-by `shelf_life` days later."""
    bought = datetime.now(timezone.utc) - timedelta(days=days_ago)
    db.add(
        InventoryEvent(
            event_id=uuid.uuid4(),
            event_type="purchased",
            item_id=item.id,
            lot_id=lot_id or uuid.uuid4(),
            quantity=1,
            unit="each",
            occurred_at=bought,
            expiration_date=(bought.date() + timedelta(days=shelf_life)) if shelf_life is not None else None,
        )
    )
    db.commit()


def _relabel(db, item, *, lot_id, expiration_date: date):
    db.add(
        InventoryEvent(
            event_id=uuid.uuid4(),
            event_type="relabeled",
            item_id=item.id,
            lot_id=lot_id,
            quantity=0,
            unit="each",
            occurred_at=datetime.now(timezone.utc),
            expiration_date=expiration_date,
        )
    )
    db.commit()


def test_says_nothing_with_no_history(db_session, item):
    assert learned_shelf_life(db_session, item.id) is None


def test_says_nothing_below_the_threshold(db_session, item):
    for i in range(MIN_OBSERVATIONS - 1):
        _purchase(db_session, item, days_ago=30 - i, shelf_life=14)
    assert learned_shelf_life(db_session, item.id) is None


def test_learns_a_consistent_shelf_life(db_session, item):
    # The chicken case: bought three times, best-by about a fortnight each time.
    for i, life in enumerate((14, 13, 15)):
        _purchase(db_session, item, days_ago=40 - i * 10, shelf_life=life)

    hint = learned_shelf_life(db_session, item.id)
    assert hint is not None
    assert hint.days == 14
    assert hint.observations == 3
    assert hint.spread_days == 2


def test_a_mistyped_year_is_outvoted_not_averaged(db_session, item):
    # A "2027" for "2026" slip adds ~365 days. The median steps over it; a mean
    # would land at ~131 days and confidently suggest it.
    for i, life in enumerate((14, 13, 379, 15)):
        _purchase(db_session, item, days_ago=50 - i * 10, shelf_life=life)

    hint = learned_shelf_life(db_session, item.id)
    # It is caught by the spread gate rather than smoothed over: one wild value
    # means we do not yet know this item well enough to speak.
    assert hint is None


def test_says_nothing_when_observations_disagree(db_session, item):
    # 3 vs 17 days is not one shelf life with noise, it is two different things.
    for i, life in enumerate((3, 17, 9)):
        _purchase(db_session, item, days_ago=40 - i * 10, shelf_life=life)
    assert learned_shelf_life(db_session, item.id) is None


def test_long_dated_goods_tolerate_proportionally_more_spread(db_session, item):
    # A can dated 400 vs 550 days out is consistent — the printed date tracks
    # manufacture and warehouse time varies. Being 70 days off costs nothing.
    for i, life in enumerate((400, 550, 480)):
        _purchase(db_session, item, days_ago=60 - i * 10, shelf_life=life)

    hint = learned_shelf_life(db_session, item.id)
    assert hint is not None
    assert hint.days == 480


def test_short_shelf_lives_get_an_absolute_allowance(db_session, item):
    # 2/3/4 days spans 100% of its median but is obviously "about 3 days".
    for i, life in enumerate((2, 3, 4)):
        _purchase(db_session, item, days_ago=20 - i * 5, shelf_life=life)

    hint = learned_shelf_life(db_session, item.id)
    assert hint is not None
    assert hint.days == 3


def test_purchases_without_a_best_by_are_not_observations(db_session, item):
    # Skipping the field tells us nothing — it must not read as a zero-day life.
    for i in range(5):
        _purchase(db_session, item, days_ago=40 - i * 5, shelf_life=None)
    assert learned_shelf_life(db_session, item.id) is None


def test_a_best_by_already_past_at_check_in_is_ignored(db_session, item):
    # Clearance stickers and typos, not shelf lives.
    for i, life in enumerate((14, 13, 15)):
        _purchase(db_session, item, days_ago=40 - i * 10, shelf_life=life)
    _purchase(db_session, item, days_ago=5, shelf_life=-30)

    hint = learned_shelf_life(db_session, item.id)
    assert hint is not None
    assert hint.observations == 3


def test_learns_retroactively_from_a_later_relabel(db_session, item):
    """Filling in a best-by after the fact counts — that is the 'I looked up
    what I threw away' path, and the log is append-only so it arrives as a
    separate `relabeled` event."""
    lots = [uuid.uuid4() for _ in range(3)]
    for i, lot in enumerate(lots):
        _purchase(db_session, item, days_ago=40 - i * 10, shelf_life=None, lot_id=lot)

    assert learned_shelf_life(db_session, item.id) is None, "no dates yet, so no opinion"

    # Go back and record what each one was actually dated.
    for i, lot in enumerate(lots):
        # Reconstruct the UTC purchase fixture date, not the runner's local day.
        bought = datetime.now(timezone.utc).date() - timedelta(days=40 - i * 10)
        _relabel(db_session, item, lot_id=lot, expiration_date=bought + timedelta(days=14))

    hint = learned_shelf_life(db_session, item.id)
    assert hint is not None
    assert hint.days == 14
    assert hint.observations == 3


def test_a_correction_replaces_the_value_it_corrects(db_session, item):
    """A relabel must not be counted *alongside* the wrong date it fixes."""
    lots = [uuid.uuid4() for _ in range(3)]
    for i, lot in enumerate(lots):
        _purchase(db_session, item, days_ago=40 - i * 10, shelf_life=14, lot_id=lot)

    # One was typed as 4 days when it should have been 14; fix it.
    bought = datetime.now(timezone.utc).date() - timedelta(days=40)
    _relabel(db_session, item, lot_id=lots[0], expiration_date=bought + timedelta(days=90))

    hint = learned_shelf_life(db_session, item.id)
    # Three lots, three observations — not four. The corrected value is 90, so
    # they now disagree and the estimator holds its tongue.
    assert hint is None


def test_leftovers_get_a_best_by_so_they_reach_the_expiring_soon_list(client):
    """A cooked dish is never *purchased*, so it accumulates no observations and
    would otherwise sit in inventory undated — invisible to the list whose whole
    job is to say what to eat first."""
    from modules.catalog.services.shelf_life import LEFTOVER_SHELF_LIFE_DAYS

    loc = client.post("/api/locations", json={"name": f"Fridge {uuid.uuid4().hex[:6]}"}).json()
    flour = client.post("/api/items", json={"name": f"Flour {uuid.uuid4().hex[:6]}"}).json()
    bread = client.post("/api/items", json={"name": f"Bread {uuid.uuid4().hex[:6]}"}).json()
    client.post(
        "/api/inventory/add",
        json={"item_id": flour["id"], "location_id": loc["id"], "quantity": 10, "unit": "each"},
    )

    recipe = client.post(
        "/api/recipes",
        json={
            "name": f"Loaf {uuid.uuid4().hex[:6]}",
            "servings": 4,
            "output_item_id": bread["id"],
            "output_quantity": 1,
            "output_unit": "each",
            "ingredients": [{"item_id": flour["id"], "quantity_required": 2, "unit": "each"}],
        },
    ).json()

    cooked = client.post(f"/api/recipes/{recipe['id']}/cook", json={"servings": 1})
    assert cooked.status_code == 200, cooked.text

    due = date.today() + timedelta(days=LEFTOVER_SHELF_LIFE_DAYS)
    on_hand = client.get("/api/inventory", params={"location_id": loc["id"]}).json()["items"]
    line = next(r for r in on_hand if r["item_id"] == bread["id"])
    assert line["expiration_date"] == str(due)

    # The point of the date: the dish now shows up in what-to-eat-first.
    expiring = client.get(f"/api/insights/expiring/{loc['id']}", params={"within_days": 7}).json()
    assert any(row["item_id"] == bread["id"] for row in expiring["expiring_items"])

