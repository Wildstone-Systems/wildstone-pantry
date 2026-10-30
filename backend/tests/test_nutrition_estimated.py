"""Estimated-nutrition fallback (ADR 0013 follow-up).

When no provider carries label macros for a product's UPC — common for retailer
house brands — we borrow GENERIC per-100g macros from USDA by NAME and flag them
`nutrition_estimated` so the UI never presents them as label-exact.
"""

from __future__ import annotations

import httpx

from app.core.config import settings
from modules.catalog.services import barcode_providers as providers
from modules.catalog.services import barcodes as barcodes_service
from modules.catalog.services.barcode_providers import NutritionEstimate, ProductResult


class _FakeResp:
    def __init__(self, payload: dict) -> None:
        self._payload = payload

    def json(self) -> dict:
        return self._payload


def test_estimate_by_name_requires_word_overlap(monkeypatch) -> None:
    """A generic food only counts if it shares a real word with the query, so a
    non-food ('paper towels') can't quietly borrow some food's macros."""
    foods = {
        "foods": [
            {
                "dataType": "Foundation",
                "description": "Oil, olive, extra virgin",
                "foodNutrients": [{"nutrientNumber": "208", "value": 884}],
            }
        ]
    }
    monkeypatch.setattr(providers, "_provider_get", lambda *a, **k: _FakeResp(foods))

    got = providers.estimate_nutrition_by_name("Pompeian Olive Oil", usda_api_key="k")
    assert got and got.nutrition["energy-kcal_100g"] == 884
    # The matched USDA reference is carried through for auditing.
    assert got.source_description == "Oil, olive, extra virgin"

    assert providers.estimate_nutrition_by_name("Paper Towels", usda_api_key="k") is None


def test_estimate_skips_branded_and_takes_generic(monkeypatch) -> None:
    foods = {
        "foods": [
            {
                "dataType": "Branded",
                "description": "Kidney Beans",
                "foodNutrients": [{"nutrientNumber": "208", "value": 300}],
            },
            {
                "dataType": "SR Legacy",
                "description": "Beans, kidney, red, canned",
                "foodNutrients": [{"nutrientNumber": "208", "value": 84}],
            },
        ]
    }
    monkeypatch.setattr(providers, "_provider_get", lambda *a, **k: _FakeResp(foods))
    got = providers.estimate_nutrition_by_name("kidney beans", usda_api_key="k")
    assert got.nutrition["energy-kcal_100g"] == 84  # generic, not the branded miss
    assert got.source_description == "Beans, kidney, red, canned"


def test_estimate_dormant_without_key(monkeypatch) -> None:
    monkeypatch.setattr(settings, "usda_fdc_api_key", "")
    assert providers.estimate_nutrition_by_name("kidney beans", usda_api_key="") is None


def test_rescan_applies_estimated_macros(client: httpx.Client, monkeypatch) -> None:
    """A checked matching candidate is persisted with an estimate flag."""
    from modules.automation import food_judge
    monkeypatch.setattr(food_judge, "same_food", lambda *a, **k: food_judge.FoodVerdict(True, "same food"))
    monkeypatch.setattr(
        barcodes_service,
        "lookup_external_merged",
        lambda barcode, **_k: ProductResult(barcode=barcode, name="Kroger Kidney Beans", source="kroger"),
    )
    monkeypatch.setattr(
        barcodes_service,
        "estimate_nutrition_by_name",
        lambda name, **_k: NutritionEstimate(
            nutrition={"energy-kcal_100g": 84, "proteins_100g": 5.2},
            source_description="Beans, kidney, red, canned",
            fdc_id=16033,
        ),
    )
    item = client.post("/api/items", json={"name": "kidney beans", "barcode": "0001111071787"}).json()
    r = client.post(f"/api/items/{item['id']}/rescan")
    assert r.status_code == 200, r.text

    got = client.get(f"/api/items/{item['id']}").json()
    assert got["energy_kcal_100g"] == 84
    assert got["protein_g_100g"] == 5.2
    assert got["nutrition_estimated"] is True
    # The USDA reference is recorded so a wrong match is auditable.
    assert got["nutrition_source"] == "Beans, kidney, red, canned · USDA FDC 16033"


def test_rescan_estimates_nutrition_for_no_upc_item(client: httpx.Client, monkeypatch) -> None:
    """A no-UPC item (e.g. home-grown eggs) can still get macros: rescan falls back
    to a USDA name search and flags the result estimated."""

    from modules.automation import food_judge
    monkeypatch.setattr(food_judge, "same_food", lambda *a, **k: food_judge.FoodVerdict(True, "same food"))

    def _no_barcode_lookup(*_a, **_k):
        raise AssertionError("no barcode to look up — must not hit the barcode provider")

    monkeypatch.setattr(barcodes_service, "lookup_external_merged", _no_barcode_lookup)
    monkeypatch.setattr(
        barcodes_service,
        "estimate_nutrition_by_name",
        lambda name, **_k: NutritionEstimate(
            nutrition={"energy-kcal_100g": 143, "proteins_100g": 12.6},
            source_description="Egg, whole, raw, fresh",
            fdc_id=748967,
        ),
    )

    item = client.post("/api/items", json={"name": "Chicken Egg (Wild Hearts Farm)"}).json()
    assert (item.get("barcode") or "") == ""

    r = client.post(f"/api/items/{item['id']}/rescan")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "updated"
    assert body["estimated"] is True
    # The response carries the reference so the UI can show what it matched.
    assert body["estimated_source"] == "Egg, whole, raw, fresh · USDA FDC 748967"

    got = client.get(f"/api/items/{item['id']}").json()
    assert got["energy_kcal_100g"] == 143
    assert got["protein_g_100g"] == 12.6
    assert got["nutrition_estimated"] is True
    assert got["nutrition_source"] == "Egg, whole, raw, fresh · USDA FDC 748967"


def test_rescan_no_upc_no_match_reports_cleanly(client: httpx.Client, monkeypatch) -> None:
    monkeypatch.setattr(barcodes_service, "estimate_nutrition_by_name", lambda name, **_k: None)
    item = client.post("/api/items", json={"name": "Mystery Homegrown Thing"}).json()
    body = client.post(f"/api/items/{item['id']}/rescan").json()
    assert body["status"] == "no_barcode"
    assert body["estimated"] is False


def test_nutrition_candidates_lists_top_choices(client: httpx.Client, monkeypatch) -> None:
    """The picker offers a few USDA foods with macro previews so a wrong match
    (sour cream vs heavy cream) is a visible choice, not a silent guess."""
    monkeypatch.setattr(
        barcodes_service,
        "search_nutrition_candidates",
        lambda name, **_k: [
            NutritionEstimate(
                nutrition={"energy-kcal_100g": 193, "proteins_100g": 2.4, "fat_100g": 19, "carbohydrates_100g": 4.6},
                source_description="Sour cream, cultured",
                fdc_id=171256,
            ),
            NutritionEstimate(
                nutrition={"energy-kcal_100g": 340, "proteins_100g": 2.8, "fat_100g": 36, "carbohydrates_100g": 2.8},
                source_description="Cream, heavy whipping",
                fdc_id=170859,
            ),
        ],
    )
    item = client.post("/api/items", json={"name": "Sour Cream"}).json()
    res = client.get(f"/api/items/{item['id']}/nutrition-candidates")
    assert res.status_code == 200, res.text
    cands = res.json()["candidates"]
    assert [c["description"] for c in cands] == ["Sour cream, cultured", "Cream, heavy whipping"]
    assert cands[0]["calories"] == 193 and cands[0]["fdc_id"] == 171256


def test_usda_food_by_fdc_id_parses_detail_schema(monkeypatch) -> None:
    """The /food/{fdcId} detail response nests the code under nutrient.number and
    uses `amount` — the picker's 'Use this' silently failed to parse it before."""
    detail = {
        "description": "Egg, whole, raw, fresh",
        "fdcId": 748967,
        "dataType": "Foundation",
        "foodNutrients": [
            {"nutrient": {"number": "208"}, "amount": 143},
            {"nutrient": {"number": "203"}, "amount": 12.6},
        ],
    }
    monkeypatch.setattr(providers, "_provider_get", lambda *a, **k: _FakeResp(detail))
    est = providers.usda_food_by_fdc_id(748967, usda_api_key="k")
    assert est and est.nutrition["energy-kcal_100g"] == 143
    assert est.nutrition["proteins_100g"] == 12.6
    assert est.source_description == "Egg, whole, raw, fresh"


def test_nutrition_candidates_reports_throttled(client: httpx.Client, monkeypatch) -> None:
    def _boom(*_a, **_k):
        raise providers.ProviderRateLimited("USDA")

    monkeypatch.setattr(barcodes_service, "search_nutrition_candidates", _boom)
    item = client.post("/api/items", json={"name": "Egg"}).json()
    res = client.get(f"/api/items/{item['id']}/nutrition-candidates").json()
    assert res["throttled"] is True
    assert res["candidates"] == []


def test_nutrition_candidates_honors_query_override(client: httpx.Client, monkeypatch) -> None:
    """The picker can refine the search term (item name buries 'egg' under salad)."""
    seen: dict = {}

    def _search(term, **_k):
        seen["term"] = term
        return [NutritionEstimate(nutrition={"energy-kcal_100g": 143}, source_description="Egg, whole, raw", fdc_id=1)]

    monkeypatch.setattr(barcodes_service, "search_nutrition_candidates", _search)
    item = client.post("/api/items", json={"name": "Chicken Egg (Wild Hearts Farm)"}).json()

    # No override → searches the item name.
    client.get(f"/api/items/{item['id']}/nutrition-candidates")
    assert seen["term"] == "Chicken Egg (Wild Hearts Farm)"

    # Override → searches the refined query.
    client.get(f"/api/items/{item['id']}/nutrition-candidates", params={"q": "egg whole raw"})
    assert seen["term"] == "egg whole raw"


def test_apply_chosen_usda_food_overwrites_and_records_source(client: httpx.Client, monkeypatch) -> None:
    # Apply resolves the chosen food from the SAME name search (matched by fdc id),
    # not a second detail fetch.
    monkeypatch.setattr(
        barcodes_service,
        "search_nutrition_candidates",
        lambda term, **_k: [
            NutritionEstimate(
                nutrition={"energy-kcal_100g": 193, "proteins_100g": 2.4, "fat_100g": 19, "carbohydrates_100g": 4.6},
                source_description="Sour cream, cultured",
                fdc_id=171256,
            ),
            NutritionEstimate(
                nutrition={"energy-kcal_100g": 340}, source_description="Cream, heavy whipping", fdc_id=170859
            ),
        ],
    )
    # A prior WRONG estimate (heavy cream numbers) that the user is correcting.
    item = client.post("/api/items", json={"name": "Sour Cream", "energy_kcal_100g": 340}).json()

    applied = client.post(f"/api/items/{item['id']}/nutrition-from-usda", json={"fdc_id": 171256})
    assert applied.status_code == 200, applied.text
    body = applied.json()
    assert body["energy_kcal_100g"] == 193  # overwritten, not fill-null
    assert body["protein_g_100g"] == 2.4
    assert body["nutrition_estimated"] is True
    assert body["nutrition_source"] == "Sour cream, cultured · USDA FDC 171256"


def test_apply_unknown_fdc_id_errors_cleanly(client: httpx.Client, monkeypatch) -> None:
    monkeypatch.setattr(
        barcodes_service,
        "search_nutrition_candidates",
        lambda term, **_k: [
            NutritionEstimate(nutrition={"energy-kcal_100g": 193}, source_description="Sour cream", fdc_id=1)
        ],
    )
    item = client.post("/api/items", json={"name": "Sour Cream"}).json()
    res = client.post(f"/api/items/{item['id']}/nutrition-from-usda", json={"fdc_id": 999999})
    assert res.status_code == 404
    assert "search again" in res.json()["detail"]


def test_label_nutrition_is_not_flagged_estimated(client: httpx.Client, monkeypatch) -> None:
    """When the provider DOES have macros, the estimate path is never consulted."""
    monkeypatch.setattr(
        barcodes_service,
        "lookup_external_merged",
        lambda barcode, **_k: ProductResult(
            barcode=barcode, name="Branded Beans", source="open_food_facts", nutrition={"energy-kcal_100g": 90}
        ),
    )

    def _explode(*_a, **_k):
        raise AssertionError("estimate must not run when label macros exist")

    monkeypatch.setattr(barcodes_service, "estimate_nutrition_by_name", _explode)
    item = client.post("/api/items", json={"name": "beans", "barcode": "0002222071787"}).json()
    client.post(f"/api/items/{item['id']}/rescan")

    got = client.get(f"/api/items/{item['id']}").json()
    assert got["energy_kcal_100g"] == 90
    assert got["nutrition_estimated"] is False
