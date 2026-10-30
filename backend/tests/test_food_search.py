"""Free-text USDA food search for logging a meal with no barcode and no pantry
match ("3 hard boiled eggs") — a standalone picker (no existing item required)
that auto-creates a lightweight, barcode-less Item from the chosen result, then
plates off-pantry from it exactly like a barcode scan would.
"""

from __future__ import annotations

from decimal import Decimal

import httpx

from modules.catalog.services import barcode_providers as providers
from modules.catalog.services import barcodes as barcodes_service
from modules.catalog.services.barcode_providers import NutritionEstimate


class _FakeResp:
    def __init__(self, payload: dict) -> None:
        self._payload = payload

    def json(self) -> dict:
        return self._payload


def test_grams_per_each_skips_spoon_and_cup_portions(monkeypatch) -> None:
    """Regression: USDA lists a food's portions in an arbitrary order — for
    "Egg, whole, cooked, hard-boiled" (fdc 173424, real API response), "1
    tbsp" (8.5 g) comes BEFORE "1 large" (50 g). Blindly taking the first
    portion with a gramWeight would set grams_per_each to a spoonful, making
    "3 each" resolve to ~25.5 g total instead of ~150 g."""
    detail = {
        "foodPortions": [
            {"gramWeight": 8.5, "amount": 1.0, "modifier": "tbsp"},
            {"gramWeight": 136.0, "amount": 1.0, "modifier": "cup, chopped"},
            {"gramWeight": 50.0, "amount": 1.0, "modifier": "large"},
        ]
    }
    monkeypatch.setattr(providers, "_provider_get", lambda *a, **k: _FakeResp(detail))
    result = providers.usda_food_portion_grams_per_each(173424, api_key="k")
    assert result == Decimal("50.0")


def test_grams_per_each_none_when_only_spoon_cup_portions_exist(monkeypatch) -> None:
    detail = {"foodPortions": [{"gramWeight": 8.5, "amount": 1.0, "modifier": "tbsp"}]}
    monkeypatch.setattr(providers, "_provider_get", lambda *a, **k: _FakeResp(detail))
    assert providers.usda_food_portion_grams_per_each(173424, api_key="k") is None


def _admin_headers(client: httpx.Client) -> dict[str, str]:
    client.post("/api/auth/bootstrap-admin", json={"email": "admin@pantryos.local", "password": "TestPass123!"})
    login = client.post("/api/auth/login", json={"email": "admin@pantryos.local", "password": "TestPass123!"})
    assert login.status_code == 200
    return {"Authorization": f"Bearer {login.json()['token']}"}


def _candidates(**_kwargs):
    return [
        NutritionEstimate(
            nutrition={"energy-kcal_100g": 155, "proteins_100g": 12.6, "fat_100g": 10.6, "carbohydrates_100g": 1.1},
            source_description="EGG, WHOLE, COOKED, HARD-BOILED",
            fdc_id=173424,
            data_type="SR Legacy",
        )
    ]


def _mock_portions(monkeypatch, grams_per_each) -> None:
    # usda_food_portion_grams_per_each is a SEPARATE by-id call (search results
    # never carry portion data — see the function's docstring), mocked
    # independently of search_nutrition_candidates.
    monkeypatch.setattr(barcodes_service, "usda_food_portion_grams_per_each", lambda fdc_id, **k: grams_per_each)


def test_search_nutrition_route_lists_candidates(client: httpx.Client, monkeypatch) -> None:
    monkeypatch.setattr(barcodes_service, "search_nutrition_candidates", lambda term, **k: _candidates())
    res = client.get("/api/items/search-nutrition", params={"q": "hard boiled egg"})
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["throttled"] is False
    assert len(body["candidates"]) == 1
    cand = body["candidates"][0]
    assert cand["fdc_id"] == 173424
    assert cand["description"] == "EGG, WHOLE, COOKED, HARD-BOILED"
    assert cand["calories"] == 155


def test_search_nutrition_route_reports_throttled(client: httpx.Client, monkeypatch) -> None:
    def _boom(*_a, **_k):
        raise providers.ProviderRateLimited("USDA")

    monkeypatch.setattr(barcodes_service, "search_nutrition_candidates", _boom)
    res = client.get("/api/items/search-nutrition", params={"q": "egg"})
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["throttled"] is True
    assert body["candidates"] == []


def test_from_food_search_creates_item_with_structured_macros(client: httpx.Client, monkeypatch) -> None:
    monkeypatch.setattr(barcodes_service, "search_nutrition_candidates", lambda term, **k: _candidates())
    _mock_portions(monkeypatch, Decimal("50"))
    res = client.post("/api/items/from-food-search", json={"fdc_id": 173424, "q": "hard boiled egg"})
    assert res.status_code == 201, res.text
    body = res.json()
    assert body["name"] == "Egg, Whole, Cooked, Hard-Boiled"
    assert body["source"] == "usda_fdc_generic"
    assert body["usda_fdc_id"] == 173424
    assert float(body["energy_kcal_100g"]) == 155
    assert float(body["protein_g_100g"]) == 12.6
    assert body["stock_mode"] == "measured"
    assert body["base_unit"] == "g"
    assert body["nutrition_estimated"] is True
    assert float(body["grams_per_each"]) == 50  # USDA's "1 large" reference weight


def test_from_food_search_dedupes_on_fdc_id(client: httpx.Client, monkeypatch) -> None:
    monkeypatch.setattr(barcodes_service, "search_nutrition_candidates", lambda term, **k: _candidates())
    _mock_portions(monkeypatch, Decimal("50"))
    first = client.post("/api/items/from-food-search", json={"fdc_id": 173424, "q": "hard boiled egg"})
    assert first.status_code == 201, first.text
    first_id = first.json()["id"]

    # Second pick of the SAME fdc_id reuses the item — already has a
    # grams_per_each, so dedup must short-circuit before searching OR
    # fetching portions again.
    def _boom(*_a, **_k):
        raise AssertionError("should not be called — the fdc_id already resolved to a fully-backfilled item")

    monkeypatch.setattr(barcodes_service, "search_nutrition_candidates", _boom)
    monkeypatch.setattr(barcodes_service, "usda_food_portion_grams_per_each", _boom)
    second = client.post("/api/items/from-food-search", json={"fdc_id": 173424, "q": "hard boiled egg"})
    assert second.status_code == 201, second.text
    assert second.json()["id"] == first_id


def test_from_food_search_backfills_grams_per_each_on_an_older_item(client: httpx.Client, monkeypatch) -> None:
    """An item created before portion weights were captured (or when USDA had
    none the first time) gets backfilled next time it's picked, once USDA has
    the data — self-healing, no migration/backfill script needed. Backfill is
    a single by-id portions lookup — it must NOT re-search by name (the actual
    live bug: re-searching a comma-heavy title against /foods/search is flaky
    and pointless when all that's missing is the portion weight)."""
    monkeypatch.setattr(barcodes_service, "search_nutrition_candidates", lambda term, **k: _candidates())
    _mock_portions(monkeypatch, None)
    first = client.post("/api/items/from-food-search", json={"fdc_id": 173424, "q": "hard boiled egg"})
    assert first.status_code == 201, first.text
    assert first.json()["grams_per_each"] is None
    item_id = first.json()["id"]

    def _boom(*_a, **_k):
        raise AssertionError("backfill must not re-search by name")

    monkeypatch.setattr(barcodes_service, "search_nutrition_candidates", _boom)
    _mock_portions(monkeypatch, Decimal("50"))
    second = client.post("/api/items/from-food-search", json={"fdc_id": 173424, "q": "hard boiled egg"})
    assert second.status_code == 201, second.text
    assert second.json()["id"] == item_id
    assert float(second.json()["grams_per_each"]) == 50


def test_from_food_search_unknown_fdc_id_in_results_404s(client: httpx.Client, monkeypatch) -> None:
    monkeypatch.setattr(barcodes_service, "search_nutrition_candidates", lambda term, **k: _candidates())
    res = client.post("/api/items/from-food-search", json={"fdc_id": 999999, "q": "hard boiled egg"})
    assert res.status_code == 404


def test_food_search_item_plates_off_pantry_with_macros(client: httpx.Client, monkeypatch) -> None:
    """End-to-end: search → create → plate 150 g (≈3 eggs) attributed to a
    profile, off-pantry (no location), with calories/macros resolved from the
    per-100g figures — the whole point of the feature."""
    h = _admin_headers(client)
    monkeypatch.setattr(barcodes_service, "search_nutrition_candidates", lambda term, **k: _candidates())
    created = client.post("/api/items/from-food-search", json={"fdc_id": 173424, "q": "hard boiled egg"}, headers=h)
    item_id = created.json()["id"]

    profile = client.post("/api/household/profiles", json={"display_name": "Alex"}, headers=h).json()["id"]
    plate = client.post(
        "/api/consumption/plate",
        json={"item_id": item_id, "quantity": 150, "unit": "g"},
        headers={**h, "X-Household-Profile": profile},
    )
    assert plate.status_code == 200, plate.text
    body = plate.json()
    assert body["location_id"] is None
    assert body["quantity"] == "150" or float(body["quantity"]) == 150
    assert body["unit"] == "g"
    assert round(float(body["calories"])) == round(155 * 1.5)  # 155 kcal/100g * 1.5


def test_picking_a_result_survives_a_flaky_search(client: httpx.Client, monkeypatch) -> None:
    """The reported bug: click a visible search result, get "that USDA food
    wasn't in the results".

    Resolution used to RE-RUN the search and match on fdc_id, so it depended on
    a second round-trip reproducing the first. USDA's /foods/search is flaky —
    the identical query measurably returns 25 results one moment and 400s (zero
    results) the next. Resolving by id can't drift that way.
    """
    def _dead_search(*_a, **_k):
        return []  # the search that just worked now returns nothing

    monkeypatch.setattr(barcodes_service, "search_nutrition_candidates", _dead_search)
    monkeypatch.setattr(
        barcodes_service,
        "usda_food_by_fdc_id",
        lambda fdc_id, **k: NutritionEstimate(
            nutrition={"energy-kcal_100g": 211, "proteins_100g": 21.0},
            source_description="Pork, fresh, loin, center rib, bone-in, raw",
            fdc_id=fdc_id,
            data_type="SR Legacy",
        ),
    )
    _mock_portions(monkeypatch, None)

    res = client.post("/api/items/from-food-search", json={"fdc_id": 167833, "q": "pork ribs"})
    assert res.status_code == 201, res.text
    assert res.json()["usda_fdc_id"] == 167833
    assert float(res.json()["energy_kcal_100g"]) == 211


def test_falls_back_to_the_search_when_the_detail_record_is_unusable(
    client: httpx.Client, monkeypatch
) -> None:
    """Some foods' detail records don't parse; the re-search stays as a backstop."""
    monkeypatch.setattr(barcodes_service, "usda_food_by_fdc_id", lambda fdc_id, **k: None)
    monkeypatch.setattr(barcodes_service, "search_nutrition_candidates", lambda term, **k: _candidates())
    _mock_portions(monkeypatch, None)

    res = client.post("/api/items/from-food-search", json={"fdc_id": 173424, "q": "hard boiled egg"})
    assert res.status_code == 201, res.text
    assert res.json()["usda_fdc_id"] == 173424


def test_still_404s_when_neither_path_can_find_it(client: httpx.Client, monkeypatch) -> None:
    monkeypatch.setattr(barcodes_service, "usda_food_by_fdc_id", lambda fdc_id, **k: None)
    monkeypatch.setattr(barcodes_service, "search_nutrition_candidates", lambda term, **k: [])
    res = client.post("/api/items/from-food-search", json={"fdc_id": 999999, "q": "nothing"})
    assert res.status_code == 404
