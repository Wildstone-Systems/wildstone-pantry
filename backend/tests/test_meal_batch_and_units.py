"""Deterministic meal logging: only offer units that convert, and log a whole
meal in one confirmation.

The reported problem: scan a product, pick a unit from a fixed list that doesn't
convert for THAT item, submit, and get an entry worth zero calories — then repeat
the whole workflow per item for a bar + chips + soda.
"""

from __future__ import annotations

from decimal import Decimal

import httpx


def _admin(client: httpx.Client) -> dict[str, str]:
    client.post("/api/auth/bootstrap-admin", json={"email": "a@b.c", "password": "TestPass123!"})
    t = client.post("/api/auth/login", json={"email": "a@b.c", "password": "TestPass123!"}).json()["token"]
    return {"Authorization": f"Bearer {t}"}


def test_loggable_units_offers_only_what_converts(client: httpx.Client) -> None:
    h = _admin(client)
    # A protein bar: per-100g nutrition and a known weight per bar, but no
    # density and no serving size — so 'each' and mass units work, volume doesn't.
    bar = client.post(
        "/api/items",
        json={"name": "Protein Bar", "energy_kcal_100g": 400, "protein_g_100g": 33, "grams_per_each": 60},
        headers=h,
    ).json()["id"]

    body = client.get(f"/api/items/{bar}/loggable-units", headers=h).json()
    usable = {u["unit"] for u in body["units"]}
    unresolvable = {u["unit"] for u in body["unresolvable"]}

    assert "each" in usable and "g" in usable and "oz" in usable
    assert "cup" in unresolvable and "serving" in unresolvable
    assert body["recommended_unit"] == "each"  # what a human says for a bar
    assert body["has_nutrition"] is True

    # One bar = 60 g = 240 kcal, so the UI can show the total before submitting.
    each = next(u for u in body["units"] if u["unit"] == "each")
    assert Decimal(each["grams"]) == Decimal("60")
    assert Decimal(each["calories"]) == Decimal("240")


def test_unresolvable_units_explain_what_is_missing(client: httpx.Client) -> None:
    h = _admin(client)
    item = client.post("/api/items", json={"name": "Mystery Powder", "energy_kcal_100g": 100}, headers=h).json()["id"]
    body = client.get(f"/api/items/{item}/loggable-units", headers=h).json()
    reasons = {u["unit"]: u["reason"] for u in body["unresolvable"]}
    assert "grams_per_each" in reasons["each"]
    assert "serving_size_g" in reasons["serving"]
    assert "density" in reasons["cup"]
    # Mass still works, so it's loggable — just by weight.
    assert body["recommended_unit"] == "g"


def test_item_with_no_nutrition_says_so_up_front(client: httpx.Client) -> None:
    h = _admin(client)
    item = client.post("/api/items", json={"name": "Leftovers"}, headers=h).json()["id"]
    body = client.get(f"/api/items/{item}/loggable-units", headers=h).json()
    assert body["has_nutrition"] is False  # loggable, but contributes no calories


def test_batch_logs_a_whole_meal_in_one_confirmation(client: httpx.Client) -> None:
    h = _admin(client)
    profile = client.post("/api/household/profiles", json={"display_name": "Alex"}, headers=h).json()["id"]
    bar = client.post(
        "/api/items", json={"name": "Bar", "energy_kcal_100g": 400, "grams_per_each": 60}, headers=h
    ).json()["id"]
    chips = client.post(
        "/api/items", json={"name": "Chips", "energy_kcal_100g": 500, "grams_per_each": 50}, headers=h
    ).json()["id"]
    soda = client.post(
        "/api/items", json={"name": "Soda", "energy_kcal_100g": 40, "density_g_per_ml": 1.04}, headers=h
    ).json()["id"]

    resp = client.post(
        "/api/consumption/plate-batch",
        json={
            "profile_id": profile,
            "entries": [
                {"item_id": bar, "quantity": 1, "unit": "each", "meal_type": "lunch"},
                {"item_id": chips, "quantity": 1, "unit": "each", "meal_type": "lunch"},
                {"item_id": soda, "quantity": 355, "unit": "ml", "meal_type": "lunch"},
            ],
        },
        headers=h,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert len(body["logged"]) == 3
    assert body["complete"] is True
    # 240 + 250 + ~147.7
    assert Decimal(str(body["total_calories"])) > Decimal("600")

    rollup = client.get(f"/api/consumption/calories?profile_id={profile}", headers=h).json()
    assert len(rollup["entries"]) == 3


def test_a_bad_entry_fails_the_whole_batch_with_nothing_logged(client: httpx.Client) -> None:
    """The point of confirming a batch: you never have to work out which half landed."""
    h = _admin(client)
    profile = client.post("/api/household/profiles", json={"display_name": "Sam"}, headers=h).json()["id"]
    good = client.post(
        "/api/items", json={"name": "Bar2", "energy_kcal_100g": 400, "grams_per_each": 60}, headers=h
    ).json()["id"]
    loc = client.post("/api/locations", json={"name": "Pantry"}, headers=h).json()["id"]
    short = client.post("/api/items", json={"name": "Rare Snack"}, headers=h).json()["id"]

    resp = client.post(
        "/api/consumption/plate-batch",
        json={
            "profile_id": profile,
            "entries": [
                {"item_id": good, "quantity": 1, "unit": "each"},
                # Asks to deduct stock that isn't there.
                {"item_id": short, "quantity": 5, "unit": "each", "location_id": loc},
            ],
        },
        headers=h,
    )
    assert resp.status_code == 400
    assert "in stock" in resp.json()["detail"]

    rollup = client.get(f"/api/consumption/calories?profile_id={profile}", headers=h).json()
    assert rollup["entries"] == []  # the good entry was NOT written either


def test_batch_allows_an_entry_with_no_computable_calories_but_flags_it(client: httpx.Client) -> None:
    h = _admin(client)
    profile = client.post("/api/household/profiles", json={"display_name": "Jo"}, headers=h).json()["id"]
    known = client.post(
        "/api/items", json={"name": "Bar3", "energy_kcal_100g": 400, "grams_per_each": 60}, headers=h
    ).json()["id"]
    unknown = client.post("/api/items", json={"name": "Homemade Thing"}, headers=h).json()["id"]

    resp = client.post(
        "/api/consumption/plate-batch",
        json={
            "profile_id": profile,
            "entries": [
                {"item_id": known, "quantity": 1, "unit": "each"},
                {"item_id": unknown, "quantity": 1, "unit": "each"},
            ],
        },
        headers=h,
    )
    assert resp.status_code == 200, resp.text
    # Logged, but honestly reported as an incomplete total rather than refused.
    assert resp.json()["complete"] is False
    assert len(resp.json()["logged"]) == 2


def test_over_specific_search_broadens_instead_of_returning_nothing(client: httpx.Client, monkeypatch) -> None:
    """USDA keyword search is conjunctive, so MORE words = FEWER results:
    'pork ribs bbq baby back' finds nothing while 'pork ribs' finds plenty —
    the opposite of what a user expects. Drop trailing words and retry."""
    from modules.catalog.services import barcodes as barcodes_service
    from modules.catalog.services.barcode_providers import NutritionEstimate

    asked: list[str] = []

    def _search(term, **_k):
        asked.append(term)
        if term.lower() != "pork ribs":
            return []
        return [
            NutritionEstimate(
                nutrition={"energy-kcal_100g": 300}, source_description="Pork ribs, cooked", fdc_id=1
            ),
            NutritionEstimate(
                nutrition={"energy-kcal_100g": 290}, source_description="Pork ribs, braised", fdc_id=2
            ),
            NutritionEstimate(
                nutrition={"energy-kcal_100g": 310}, source_description="Pork spareribs", fdc_id=3
            ),
        ]

    monkeypatch.setattr(barcodes_service, "search_nutrition_candidates", _search)
    h = _admin(client)
    body = client.get("/api/items/search-nutrition", params={"q": "pork ribs bbq baby back"}, headers=h).json()

    assert len(body["candidates"]) == 3
    # It reports the term that actually answered, rather than pretending the
    # original query matched.
    assert body["searched_term"] == "pork ribs"
    assert asked[0] == "pork ribs bbq baby back"  # most specific tried first


def test_search_does_not_broaden_when_the_exact_query_works(client: httpx.Client, monkeypatch) -> None:
    from modules.catalog.services import barcodes as barcodes_service
    from modules.catalog.services.barcode_providers import NutritionEstimate

    asked: list[str] = []

    def _search(term, **_k):
        asked.append(term)
        return [
            NutritionEstimate(nutrition={"energy-kcal_100g": 155}, source_description=f"Hit {i}", fdc_id=i)
            for i in range(3)
        ]

    monkeypatch.setattr(barcodes_service, "search_nutrition_candidates", _search)
    h = _admin(client)
    body = client.get("/api/items/search-nutrition", params={"q": "hard boiled egg"}, headers=h).json()
    assert body["searched_term"] == "hard boiled egg"
    assert asked == ["hard boiled egg"]  # no wasted USDA calls
