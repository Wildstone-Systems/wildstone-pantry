"""USDA-derived volume↔weight conversions, and the gate that keeps them honest.

The density itself is never asked of a language model — measured against a live
endpoint one was 11-57% wrong, including 1.0 g/tsp for black pepper where the
real figure is 2.3. USDA publishes it exactly. What a model IS good at is
judging whether the USDA food is the same food as the pantry item, which is
what stops an accurate olive-oil density being attached to dried mango.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest

from app.db import SessionLocal
from modules.catalog.services.conversions import (
    MAX_DENSITY,
    MIN_DENSITY,
    apply_density,
    density_from_portions,
    items_needing_conversion,
)

@pytest.fixture()
def db_session():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# Shaped exactly like USDA's /food/{fdcId} foodPortions.
PEPPER_PORTIONS = [
    {"amount": 1.0, "modifier": "tbsp, ground", "gramWeight": 6.9},
    {"amount": 1.0, "modifier": "dash", "gramWeight": 0.1},
    {"amount": 1.0, "modifier": "tsp, ground", "gramWeight": 2.3},
]


def test_derives_the_real_density_from_portion_rows():
    got = density_from_portions(PEPPER_PORTIONS, description="Spices, pepper, black", fdc_id=170931)
    assert got is not None
    # 2.3 g / 4.92892 ml ≈ 0.4666 g/ml — and back to the number a cook recognises.
    assert got.grams_for("tsp") == Decimal("2.30")
    assert got.grams_for("tbsp") == Decimal("6.90")
    assert got.fdc_id == 170931


def test_a_non_volume_portion_is_ignored_not_misread():
    """"1 dash -> 0.1 g" is a real row and a meaningless density. It must not
    be parsed as one."""
    got = density_from_portions(PEPPER_PORTIONS, description="x", fdc_id=1)
    assert all(p["measure"] in ("tsp", "tbsp", "cup", "fl oz") for p in got.portions)
    assert len(got.portions) == 2


def test_one_absurd_row_cannot_drag_the_answer():
    """Median, not mean — a mis-parsed row shifts a mean and the median shrugs."""
    portions = [
        {"amount": 1.0, "modifier": "tsp", "gramWeight": 2.3},
        {"amount": 1.0, "modifier": "tbsp", "gramWeight": 6.9},
        {"amount": 1.0, "modifier": "cup", "gramWeight": 400.0},  # ~1.7 g/ml, still in band
    ]
    got = density_from_portions(portions, description="x", fdc_id=1)
    assert Decimal("0.40") < got.density_g_per_ml < Decimal("0.60")


def test_physically_impossible_densities_are_dropped_entirely():
    """A wrong conversion is worse than none: the blank sends someone to look,
    the confident number does not."""
    assert density_from_portions(
        [{"amount": 1.0, "modifier": "tsp", "gramWeight": 500.0}], description="x", fdc_id=1
    ) is None
    assert density_from_portions(
        [{"amount": 1.0, "modifier": "cup", "gramWeight": 0.2}], description="x", fdc_id=1
    ) is None


def test_no_usable_portions_means_no_suggestion():
    assert density_from_portions([], description="x", fdc_id=1) is None
    assert density_from_portions(
        [{"amount": 1.0, "modifier": "serving", "gramWeight": 30.0}], description="x", fdc_id=1
    ) is None


def test_applying_a_density_is_bounds_checked(client, db_session):
    from app.models import Item

    item = Item(name=f"Spice {uuid.uuid4().hex[:6]}")
    db_session.add(item)
    db_session.commit()

    with pytest.raises(ValueError):
        apply_density(db_session, item.id, MAX_DENSITY + Decimal("1"))
    with pytest.raises(ValueError):
        apply_density(db_session, item.id, MIN_DENSITY / 2)

    updated = apply_density(db_session, item.id, Decimal("0.4666"))
    assert updated.density_g_per_ml == Decimal("0.4666")


def test_the_review_queue_only_lists_items_something_actually_needs(client, db_session):
    """282 catalogue items have no density; a queue of 282 is a wall. Only the
    ones a recipe measures by volume are blocking anything."""
    tag = uuid.uuid4().hex[:6]
    spice = client.post("/api/items", json={"name": f"Paprika {tag}"}).json()
    untouched = client.post("/api/items", json={"name": f"Nobody Uses This {tag}"}).json()
    by_weight = client.post("/api/items", json={"name": f"Rice {tag}"}).json()

    client.post(
        "/api/recipes",
        json={
            "name": f"Stew {tag}",
            "servings": 4,
            "ingredients": [
                {"item_id": spice["id"], "quantity_required": 1, "unit": "tsp"},
                {"item_id": by_weight["id"], "quantity_required": 200, "unit": "g"},
            ],
        },
    )

    queue = {row["item_id"]: row for row in items_needing_conversion(db_session)}
    assert uuid.UUID(spice["id"]) in queue, "measured by volume, stocked by count -> blocked"
    assert uuid.UUID(by_weight["id"]) not in queue, "asked for in grams, no density needed"
    assert uuid.UUID(untouched["id"]) not in queue, "no recipe wants it at all"
    assert queue[uuid.UUID(spice["id"])]["recipe_units"] == ["tsp"]


def test_the_review_queue_is_reachable_and_not_shadowed_by_the_item_route(client):
    """`/items/conversion-review` must be declared above `/items/{item_id}` or
    FastAPI parses "conversion-review" as a UUID and 422s."""
    res = client.get("/api/items/conversion-review")
    assert res.status_code == 200, res.text
    assert "items" in res.json() and "judge_available" in res.json()
