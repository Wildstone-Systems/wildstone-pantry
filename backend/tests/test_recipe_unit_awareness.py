"""ADR 0017 Phase 1 — recipes do their stock math in the item's base unit.

Before this, availability/suggestions/cook compared the recipe line's number
against on-hand as raw numbers (ADR 0016 made on-hand g/ml, so "2 cup" vs "500"
falsely read as "have"). Now every recipe boundary converts to base first, refuses
when there's no bridge (all-or-nothing preserved), and the produce side stores its
yield in base too.
"""

from __future__ import annotations

import httpx
import pytest

from app.core.config import settings


@pytest.fixture(autouse=True)
def _personal_mode(monkeypatch) -> None:
    monkeypatch.setattr(settings, "auth_enabled", False)


def _location(client: httpx.Client, name: str) -> str:
    return client.post("/api/locations", json={"name": name}).json()["id"]


def _flour(client: httpx.Client, name: str = "Flour") -> str:
    # Measured, base g, with an explicit density so cup↔g bridges without a reference.
    resp = client.post(
        "/api/items",
        json={"name": name, "stock_mode": "measured", "size_quantity": 1, "size_unit": "kg",
              "density_g_per_ml": 0.53},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def _recipe(client: httpx.Client, name: str, ingredients: list[dict], **extra) -> str:
    recipe_id = client.post("/api/recipes", json={"name": name, **extra}).json()["id"]
    r = client.put(f"/api/recipes/{recipe_id}/ingredients", json=ingredients)
    assert r.status_code in {200, 201}, r.text
    return recipe_id


def _add(client: httpx.Client, item_id: str, location_id: str, quantity: float, unit: str) -> None:
    r = client.post("/api/inventory/add",
                    json={"item_id": item_id, "location_id": location_id, "quantity": quantity, "unit": unit})
    assert r.status_code in {200, 201}, r.text


def _on_hand(client: httpx.Client, loc: str, item: str) -> float:
    rows = client.get(f"/api/inventory?location_id={loc}").json()["items"]
    return float(next((r["quantity"] for r in rows if r["item_id"] == item), 0))


def _status(client: httpx.Client, recipe_id: str, item_id: str) -> dict:
    rows = client.get(f"/api/recipes/{recipe_id}/availability").json()["ingredients"]
    return next(r for r in rows if r["item_id"] == item_id)


# 2 cup flour = 473.18 ml × 0.53 g/ml = 250.8 g.


def test_availability_converts_recipe_unit_to_base(client: httpx.Client) -> None:
    flour = _flour(client)
    loc = _location(client, "Pantry")
    recipe = _recipe(client, "Bread", [{"item_id": flour, "quantity_required": 2, "unit": "cup"}])

    # 100 g on hand is LESS than 2 cups (~250 g) — the old raw compare said "have".
    _add(client, flour, loc, 100, "g")
    row = _status(client, recipe, flour)
    assert row["status"] == "missing"
    assert row["base_unit"] == "g"
    # On-hand is echoed back in the recipe's unit for display (~0.8 cup):
    # 100 g ÷ 0.53 g/ml = 188.7 ml ÷ 236.588 ml/cup ≈ 0.8 cup.
    assert float(row["on_hand_recipe_unit"]) == pytest.approx(0.8, abs=0.05)

    # Top up past two cups → now genuinely have it.
    _add(client, flour, loc, 200, "g")  # 300 g total
    assert _status(client, recipe, flour)["status"] == "have"


def test_cook_decrements_the_converted_grams(client: httpx.Client) -> None:
    flour = _flour(client)
    loc = _location(client, "Pantry")
    recipe = _recipe(client, "Bread", [{"item_id": flour, "quantity_required": 2, "unit": "cup"}])
    _add(client, flour, loc, 300, "g")

    cooked = client.post(f"/api/recipes/{recipe}/cook", json={"servings": 1, "location_id": loc})
    assert cooked.status_code == 200, cooked.text
    # 300 - 250.8 = ~49.2 g left, NOT "300 - 2".
    assert _on_hand(client, loc, flour) == pytest.approx(49.2, abs=1.0)


def test_cook_refuses_unconvertible_line_without_spending_anything(client: httpx.Client) -> None:
    # A counted item (base each) with no size/density can't be used "by the cup".
    flour = _flour(client)
    widget = client.post("/api/items", json={"name": "Mystery Sprinkle", "stock_mode": "counted"}).json()["id"]
    loc = _location(client, "Pantry")
    _add(client, flour, loc, 300, "g")
    _add(client, widget, loc, 10, "each")

    recipe = _recipe(client, "Cake", [
        {"item_id": flour, "quantity_required": 1, "unit": "cup"},
        {"item_id": widget, "quantity_required": 1, "unit": "cup"},  # no bridge each→cup
    ])

    cooked = client.post(f"/api/recipes/{recipe}/cook", json={"servings": 1, "location_id": loc})
    assert cooked.status_code == 400, cooked.text
    assert "measure" in cooked.text.lower() or "density" in cooked.text.lower()
    # All-or-nothing: the flour was NOT touched despite being convertible/sufficient.
    assert _on_hand(client, loc, flour) == pytest.approx(300.0)
    assert _on_hand(client, loc, widget) == pytest.approx(10.0)


def test_availability_flags_stocked_but_unconvertible_as_cant_convert(client: httpx.Client) -> None:
    widget = client.post("/api/items", json={"name": "Odd Bits", "stock_mode": "counted"}).json()["id"]
    loc = _location(client, "Pantry")
    _add(client, widget, loc, 10, "each")
    recipe = _recipe(client, "Odd", [{"item_id": widget, "quantity_required": 1, "unit": "cup"}])
    row = _status(client, recipe, widget)
    # Stock exists (10 each) but there's no bridge each→volume → "can't tell" state.
    assert row["status"] == "cant_convert"


def test_missing_beats_cant_convert_when_nothing_on_hand(client: httpx.Client) -> None:
    widget = client.post("/api/items", json={"name": "Never Stocked", "stock_mode": "counted"}).json()["id"]
    _location(client, "Pantry")
    recipe = _recipe(client, "Empty", [{"item_id": widget, "quantity_required": 1, "unit": "cup"}])
    # Zero on hand → "missing" is more useful than "set a density" (precedence §4).
    assert _status(client, recipe, widget)["status"] == "missing"


def test_produce_output_stores_in_base_units(client: httpx.Client) -> None:
    flour = _flour(client)
    bread = client.post(
        "/api/items",
        json={"name": "Bread Loaf Mass", "stock_mode": "measured", "size_quantity": 1, "size_unit": "kg"},
    ).json()["id"]
    loc = _location(client, "Pantry")
    _add(client, flour, loc, 500, "g")
    recipe = _recipe(
        client, "Loaf",
        [{"item_id": flour, "quantity_required": 1, "unit": "cup"}],
        output_item_id=bread, output_quantity=1, output_unit="kg",
    )
    cooked = client.post(f"/api/recipes/{recipe}/cook", json={"servings": 1, "location_id": loc})
    assert cooked.status_code == 200, cooked.text
    # Output "1 kg" is stored as 1000 g (base), not "1 kg".
    assert _on_hand(client, loc, bread) == pytest.approx(1000.0, abs=1.0)


# --- Phase 2: standardized unit vocabulary + optional lines -------------------


def test_unit_vocab_endpoint_lists_convertible_units(client: httpx.Client) -> None:
    units = client.get("/api/recipes/unit-vocab").json()["units"]
    values = {u["value"] for u in units}
    assert {"each", "g", "kg", "ml", "l", "tsp", "tbsp", "cup", "gal"} <= values
    # Weight ounces and fluid ounces are DISTINCT entries — never collapsed.
    assert "oz" in values and "floz" in values


def test_ingredient_unit_must_be_in_the_vocabulary(client: httpx.Client) -> None:
    flour = _flour(client)
    rid = client.post("/api/recipes", json={"name": "X"}).json()["id"]
    # "pinch" isn't convertible → rejected at the API (the editor never offers it).
    bad = client.put(f"/api/recipes/{rid}/ingredients", json=[{"item_id": flour, "quantity_required": 1, "unit": "pinch"}])
    assert bad.status_code == 422, bad.text
    # A known alias is accepted and stored canonical ("cups" → "cup").
    ok = client.put(f"/api/recipes/{rid}/ingredients", json=[{"item_id": flour, "quantity_required": 1, "unit": "cups"}])
    assert ok.status_code in {200, 201}, ok.text
    got = client.get(f"/api/recipes/{rid}").json()["ingredients"][0]
    assert got["unit"] == "cup"


def test_optional_line_is_out_of_score_and_never_deducted(client: httpx.Client) -> None:
    flour = _flour(client)
    salt = client.post(
        "/api/items", json={"name": "Salt", "stock_mode": "measured", "size_quantity": 1, "size_unit": "kg"}
    ).json()["id"]
    loc = _location(client, "Pantry")
    _add(client, flour, loc, 500, "g")  # flour stocked; salt is NOT
    recipe = _recipe(client, "Bread", [
        {"item_id": flour, "quantity_required": 1, "unit": "cup"},
        {"item_id": salt, "quantity_required": 1, "unit": "tsp", "optional": True},
    ])

    # Availability: the optional salt reads "optional", not "missing".
    rows = client.get(f"/api/recipes/{recipe}/availability").json()["ingredients"]
    assert next(r for r in rows if r["item_id"] == salt)["status"] == "optional"

    # Score ignores the optional line → 100% on the flour alone.
    s = next(x for x in client.get(f"/api/recipes/suggestions/{loc}").json()["suggestions"]
             if x["recipe_id"] == recipe)
    assert s["available_score"] == pytest.approx(1.0)
    assert "Salt" not in s["missing_items"]

    # Cook succeeds without any salt on hand, and never records a salt deduction.
    cooked = client.post(f"/api/recipes/{recipe}/cook", json={"servings": 1, "location_id": loc})
    assert cooked.status_code == 200, cooked.text
    assert _on_hand(client, loc, salt) == pytest.approx(0.0)


# --- Phase 4: substitution-under-conversion (§7) + mixed-base pooling (§6) ----


def test_approved_substitute_is_measured_in_the_recipe_unit(client: httpx.Client) -> None:
    # Recipe needs 2 cup (~473 ml) of butter; oil substitutes 1:1. The old raw
    # compare (200 >= 2) would wrongly count 200 ml of oil as enough.
    butter = client.post(
        "/api/items", json={"name": "Butter", "stock_mode": "measured", "size_quantity": 1, "size_unit": "lb"}
    ).json()["id"]
    oil = client.post(
        "/api/items", json={"name": "Oil", "stock_mode": "measured", "size_quantity": 1, "size_unit": "l"}
    ).json()["id"]
    loc = _location(client, "Pantry")
    recipe = _recipe(client, "Cake", [{"item_id": butter, "quantity_required": 2, "unit": "cup"}])
    sub = client.post(
        "/api/substitutions",
        json={"from_item_id": butter, "to_item_id": oil, "ratio": 1, "outcome": "acceptable"},
    )
    assert sub.status_code in {200, 201}, sub.text

    _add(client, oil, loc, 200, "ml")  # less than 2 cups
    assert _status(client, recipe, butter)["status"] != "substitute"

    _add(client, oil, loc, 400, "ml")  # 600 ml total, now enough
    row = _status(client, recipe, butter)
    assert row["status"] == "substitute"
    assert row["available_via"]["to_canonical"] == "oil"


def test_mixed_base_pool_sums_across_units(client: httpx.Client) -> None:
    # Two "Flour" records share the canonical key but are tracked differently: one by
    # weight (g), one as bags (counted, 200 g each). A 500 g need pools BOTH — 300 g
    # + 2 bags×200 g = 700 g — by converting each into the recipe's unit (ADR 0017 §6).
    flour_g = _flour(client, "Flour")  # measured, base g
    flour_bags = client.post(
        "/api/items", json={"name": "Flour", "stock_mode": "counted", "grams_per_each": 200}
    ).json()["id"]
    loc = _location(client, "Pantry")
    recipe = _recipe(client, "Bread", [{"item_id": flour_g, "quantity_required": 500, "unit": "g"}])

    _add(client, flour_g, loc, 300, "g")  # alone, short of 500
    assert _status(client, recipe, flour_g)["status"] == "missing"

    _add(client, flour_bags, loc, 2, "each")  # +2 bags = +400 g → 700 g pooled
    assert _status(client, recipe, flour_g)["status"] == "have"


# --- PR #37 review fixes -----------------------------------------------------


def test_cook_refuses_output_that_cant_convert_to_base(client: httpx.Client) -> None:
    # Producing "1 cup" into a grams-tracked item with no density can't convert —
    # the cook must refuse BEFORE consuming, not store a mixed-unit balance.
    flour = _flour(client)
    bread = client.post(
        "/api/items", json={"name": "Mystery Bread", "stock_mode": "measured", "size_quantity": 1, "size_unit": "kg"}
    ).json()["id"]
    loc = _location(client, "Pantry")
    _add(client, flour, loc, 500, "g")
    recipe = _recipe(
        client, "Loaf", [{"item_id": flour, "quantity_required": 1, "unit": "cup"}],
        output_item_id=bread, output_quantity=1, output_unit="cup",
    )
    cooked = client.post(f"/api/recipes/{recipe}/cook", json={"servings": 1, "location_id": loc})
    assert cooked.status_code == 400, cooked.text
    assert "output" in cooked.text.lower()
    assert _on_hand(client, loc, flour) == pytest.approx(500.0)  # all-or-nothing
    assert _on_hand(client, loc, bread) == pytest.approx(0.0)


def test_container_checkin_rederives_base_for_underspecified_item(client: httpx.Client) -> None:
    # A measured item with no size defaults base to 'each'; its first ½-gal container
    # check-in must re-derive base to ml and store the stock in ml (not gal-vs-each).
    item = client.post("/api/items", json={"name": "Loose Milk", "stock_mode": "measured"}).json()
    assert item["base_unit"] == "each"
    loc = _location(client, "Fridge")
    r = client.post(
        "/api/inventory/add",
        json={"item_id": item["id"], "location_id": loc, "quantity": 1,
              "sealed_count": 1, "container_size": 0.5, "container_unit": "gal"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["added_unit"] == "ml"
    assert _on_hand(client, loc, item["id"]) == pytest.approx(1892.7, abs=1.0)
    assert client.get(f"/api/items/{item['id']}").json()["base_unit"] == "ml"


def test_substitution_honors_its_own_unit(client: httpx.Client) -> None:
    # A cross-dimension sub "1 each egg → 60 g applesauce" must be measured in grams
    # (its own unit) — the basis cook deducts in — not the recipe's "each".
    egg = client.post("/api/items", json={"name": "Egg", "stock_mode": "counted"}).json()["id"]
    applesauce = client.post(
        "/api/items", json={"name": "Applesauce", "stock_mode": "measured", "size_quantity": 1, "size_unit": "kg"}
    ).json()["id"]
    loc = _location(client, "Pantry")
    recipe = _recipe(client, "Muffins", [{"item_id": egg, "quantity_required": 2, "unit": "each"}])
    sub = client.post(
        "/api/substitutions",
        json={"from_item_id": egg, "to_item_id": applesauce, "ratio": 60, "unit": "g", "outcome": "acceptable"},
    )
    assert sub.status_code in {200, 201}, sub.text

    _add(client, applesauce, loc, 100, "g")  # 2 × 60 g = 120 g needed → short
    assert _status(client, recipe, egg)["status"] != "substitute"

    _add(client, applesauce, loc, 40, "g")  # 140 g total → covers 120 g
    row = _status(client, recipe, egg)
    assert row["status"] == "substitute"
    assert row["available_via"]["to_canonical"] == "applesauce"


def test_partial_pool_reports_cant_convert_not_missing(client: httpx.Client) -> None:
    # A pool with convertible-but-short stock AND an unconvertible contributor can't
    # be ruled out — availability should say cant_convert (as cook does), not missing.
    fg = _flour(client, "Flour")  # weight, convertible
    fb = client.post("/api/items", json={"name": "Flour", "stock_mode": "counted"}).json()["id"]  # no grams_per_each
    loc = _location(client, "Pantry")
    recipe = _recipe(client, "Bread", [{"item_id": fg, "quantity_required": 500, "unit": "g"}])
    _add(client, fg, loc, 200, "g")   # convertible, short of 500
    _add(client, fb, loc, 3, "each")  # unconvertible (each→g, no weight-per-each)
    assert _status(client, recipe, fg)["status"] == "cant_convert"
