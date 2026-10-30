"""Deterministic nutrition sanity checks.

These exist because probabilistic sources write confidently wrong numbers and
nothing downstream could tell. Arithmetic can tell, costs nothing, and cannot
itself be miscalibrated the way a model can.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest

from app.db import SessionLocal
from modules.catalog.services.plausibility import (
    IMPOSSIBLE,
    SUSPECT,
    blocking_issues,
    check_nutrition,
)


@pytest.fixture()
def db_session():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def test_more_carbohydrate_than_the_food_it_is_measured_in(client):
    """Real: 'Minced Garlic' carried 400 g of carbohydrate per 100 g."""
    issues = check_nutrition({"carbs_g_100g": Decimal("400"), "energy_kcal_100g": Decimal("100")})
    assert any(i.severity == IMPOSSIBLE and i.field == "carbs_g_100g" for i in issues)


def test_macros_that_do_not_fit_in_a_hundred_grams():
    issues = check_nutrition(
        {"protein_g_100g": Decimal("40"), "fat_g_100g": Decimal("40"), "carbs_g_100g": Decimal("40")}
    )
    assert any(i.severity == IMPOSSIBLE and i.field == "macros" for i in issues)


def test_more_energy_than_pure_fat_could_provide():
    issues = check_nutrition({"energy_kcal_100g": Decimal("1200")})
    assert any(i.severity == IMPOSSIBLE for i in issues)


def test_negative_amounts_are_impossible():
    assert blocking_issues({"protein_g_100g": Decimal("-1")})
    assert blocking_issues({"energy_kcal_100g": Decimal("-10")})


def test_ordinary_label_data_passes_cleanly():
    """Whole milk, near enough. Correct data must not be flagged, or the
    signal is worthless."""
    assert check_nutrition(
        {
            "energy_kcal_100g": Decimal("61"),
            "protein_g_100g": Decimal("3.2"),
            "fat_g_100g": Decimal("3.3"),
            "carbs_g_100g": Decimal("4.8"),
        }
    ) == []


def test_rounding_and_fibre_do_not_trip_the_energy_check():
    """Measured: correct labels disagree with Atwater by 29% at the 90th
    percentile. A tighter threshold would flag hundreds of good rows."""
    assert check_nutrition(
        {
            "energy_kcal_100g": Decimal("100"),
            "protein_g_100g": Decimal("2"),
            "fat_g_100g": Decimal("2"),
            "carbs_g_100g": Decimal("25"),  # implies 126 kcal, 26% out
        }
    ) == []


def test_energy_that_the_macros_cannot_explain_is_reported_not_refused():
    """Real: butter at 714 kcal with 11 g of fat. Wrong, but not impossible —
    fibre and polyols shift the sum legitimately, so this is surfaced, not
    rejected."""
    issues = check_nutrition(
        {
            "energy_kcal_100g": Decimal("714"),
            "protein_g_100g": Decimal("0"),
            "fat_g_100g": Decimal("11"),
            "carbs_g_100g": Decimal("0"),
        }
    )
    assert issues and all(i.severity == SUSPECT for i in issues)
    assert not blocking_issues(
        {
            "energy_kcal_100g": Decimal("714"),
            "protein_g_100g": Decimal("0"),
            "fat_g_100g": Decimal("11"),
            "carbs_g_100g": Decimal("0"),
        }
    )


def test_a_trace_calorie_drink_is_not_flagged():
    """1 kcal sparkling water is '100% off' in relative terms and perfectly
    correct — the relative check has to have a floor."""
    assert check_nutrition(
        {
            "energy_kcal_100g": Decimal("1"),
            "protein_g_100g": Decimal("0"),
            "fat_g_100g": Decimal("0"),
            "carbs_g_100g": Decimal("0"),
        }
    ) == []


def test_absent_values_say_nothing_either_way():
    assert check_nutrition({}) == []
    assert check_nutrition({"energy_kcal_100g": None, "fat_g_100g": None}) == []


# --- enforcement on write -------------------------------------------------


def test_the_api_refuses_to_store_an_impossible_item(client):
    res = client.post(
        "/api/items",
        json={"name": f"Bad {uuid.uuid4().hex[:6]}", "carbs_g_100g": 400, "energy_kcal_100g": 100},
    )
    assert res.status_code == 400, res.text
    assert "carbs" in res.json()["detail"].lower()


def test_a_partial_update_is_checked_against_what_is_already_stored(client):
    """Setting only fat can push the macro sum past 100 g. Checking the patch
    alone would miss it."""
    item = client.post(
        "/api/items",
        json={"name": f"Half {uuid.uuid4().hex[:6]}", "protein_g_100g": 50, "carbs_g_100g": 40},
    ).json()

    res = client.put(f"/api/items/{item['id']}", json={"fat_g_100g": 40})
    assert res.status_code == 400, res.text
    assert "100 g" in res.json()["detail"]


def test_a_merely_suspect_item_still_saves(client):
    """Refusing on inconsistency would throw away correct label data."""
    res = client.post(
        "/api/items",
        json={
            "name": f"Odd {uuid.uuid4().hex[:6]}",
            "energy_kcal_100g": 714,
            "protein_g_100g": 0,
            "fat_g_100g": 11,
            "carbs_g_100g": 0,
        },
    )
    assert res.status_code in (200, 201), res.text


# --- the sweep ------------------------------------------------------------


def test_the_sweep_lists_doubtful_items_with_their_provenance(client):
    tag = uuid.uuid4().hex[:6]
    client.post(
        "/api/items",
        json={
            "name": f"Suspect {tag}",
            "energy_kcal_100g": 700,
            "protein_g_100g": 0,
            "fat_g_100g": 10,
            "carbs_g_100g": 0,
        },
    )
    client.post("/api/items", json={"name": f"Fine {tag}", "energy_kcal_100g": 61,
                                    "protein_g_100g": 3.2, "fat_g_100g": 3.3, "carbs_g_100g": 4.8})

    body = client.get("/api/items/data-quality").json()
    names = {row["name"] for row in body["items"]}
    assert f"Suspect {tag}" in names
    assert f"Fine {tag}" not in names, "clean data must not be listed"

    row = next(r for r in body["items"] if r["name"] == f"Suspect {tag}")
    assert row["issues"] and row["issues"][0]["severity"] == SUSPECT
    # Provenance ships with the finding.
    assert "estimated" in row and "source" in row


def test_the_sweep_route_is_not_shadowed_by_the_item_route(client):
    assert client.get("/api/items/data-quality").status_code == 200


# --- placeholders, which no per-item check can see -------------------------


def test_identical_macros_across_many_items_read_as_a_placeholder(client, db_session):
    """Real: 23 items carried exactly 50 kcal / 2 / 1 / 8 — butter, heavy
    cream, cashews, limes and carrots alike — written by a bulk import that
    filled the fields because they were required, not because it knew.

    Every one passes the arithmetic: 4x2 + 4x8 + 9x1 = 49 against a stated 50.
    Only comparing items to EACH OTHER gives it away."""
    from modules.catalog.services.plausibility import (
        DUPLICATE_FINGERPRINT_THRESHOLD,
        audit_duplicate_macros,
    )

    tag = uuid.uuid4().hex[:6]
    for i in range(DUPLICATE_FINGERPRINT_THRESHOLD):
        client.post(
            "/api/items",
            json={
                "name": f"Placeholder{i} {tag}",
                "energy_kcal_100g": 50, "protein_g_100g": 2,
                "fat_g_100g": 1, "carbs_g_100g": 8,
            },
        )

    groups = audit_duplicate_macros(db_session)
    match = next((g for g in groups if g["energy_kcal_100g"] == 50.0), None)
    assert match is not None
    assert match["count"] >= DUPLICATE_FINGERPRINT_THRESHOLD


def test_zero_calorie_foods_agreeing_is_not_a_placeholder(client, db_session):
    """Diet drinks, black coffee and most seasonings are genuinely 0/0/0/0.
    Flagging that would park a standing false alarm at the top of the queue,
    which is how a review page stops being read."""
    from modules.catalog.services.plausibility import audit_duplicate_macros

    tag = uuid.uuid4().hex[:6]
    for name in ("Diet Cola", "Black Coffee", "Sparkling Water", "Herb Blend", "Chilli Powder"):
        client.post(
            "/api/items",
            json={
                "name": f"{name} {tag}",
                "energy_kcal_100g": 0, "protein_g_100g": 0,
                "fat_g_100g": 0, "carbs_g_100g": 0,
            },
        )

    groups = audit_duplicate_macros(db_session)
    assert all(g["energy_kcal_100g"] != 0.0 for g in groups)


def test_a_couple_of_coincidental_matches_are_left_alone(client, db_session):
    """Two foods sharing a figure is chance; four is a default someone typed."""
    from modules.catalog.services.plausibility import audit_duplicate_macros

    tag = uuid.uuid4().hex[:6]
    for i in range(2):
        client.post(
            "/api/items",
            json={"name": f"Coincidence{i} {tag}", "energy_kcal_100g": 123.45,
                  "protein_g_100g": 1.1, "fat_g_100g": 2.2, "carbs_g_100g": 3.3},
        )
    groups = audit_duplicate_macros(db_session)
    assert all(g["energy_kcal_100g"] != 123.45 for g in groups)
