"""Cooking a variable-weight roast into portioned leftovers (ADR 0025).

The evening this is built from: one 4.87 lb pork butt roast, cooked into ~4.5 lb
of pulled pork, some eaten at the table and the rest packed into three tubs.
Before this, every step of that sentence was either wrong or unsayable — the
output item refused the cook outright, the yield was a constant, and the yield
landed as one undifferentiated blob nobody could eat a single tub of.
"""

from __future__ import annotations

from decimal import Decimal

import httpx
import pytest

from app.core.config import settings

LB_G = Decimal("453.59237")


@pytest.fixture(autouse=True)
def _personal_mode(monkeypatch) -> None:
    monkeypatch.setattr(settings, "auth_enabled", False)


def _item(client: httpx.Client, **kw) -> str:
    resp = client.post("/api/items", json=kw)
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def _location(client: httpx.Client, name: str) -> str:
    return client.post("/api/locations", json={"name": name}).json()["id"]


def _on_hand(client: httpx.Client, location_id: str, item_id: str) -> Decimal:
    rows = client.get(f"/api/inventory?location_id={location_id}").json()["items"]
    match = next((r for r in rows if r["item_id"] == item_id), None)
    return Decimal(str(match["quantity"])) if match else Decimal("0")


def _lots(client: httpx.Client, item_id: str) -> list[dict]:
    return client.get(f"/api/inventory/lots?item_id={item_id}").json()["lots"]


def _pulled_pork_setup(client: httpx.Client, *, output_quantity: float = 60) -> tuple[str, str, str, str]:
    """A pork butt in the freezer and a recipe that turns 3 lb of it into pulled pork."""
    pork = _item(client, name="Pork Butt Roast", size_quantity=4.87, size_unit="lb")
    pulled = _item(client, name="Pulled Pork")
    loc = _location(client, "Fridge")
    client.post(
        "/api/inventory/add",
        json={"item_id": pork, "location_id": loc, "quantity": 4.87, "unit": "lb"},
    )
    recipe = client.post(
        "/api/recipes",
        json={
            "name": "Pulled Pork",
            "servings": 8,
            "output_item_id": pulled,
            "output_quantity": output_quantity,
            "output_unit": "oz",
            "ingredients": [{"item_id": pork, "quantity_required": 3, "unit": "lb"}],
        },
    )
    assert recipe.status_code == 200, recipe.text
    return pork, pulled, loc, recipe.json()["id"]


# --- the output item is configured by the recipe that declares it (§2) ------


def test_declaring_a_weight_output_makes_the_item_measured(client: httpx.Client) -> None:
    # A size-less item defaults to counted/'each' (ADR 0016), which cannot hold a
    # yield declared in oz — the cook used to refuse outright. Declaring the
    # output IS the statement that this thing is measured by weight.
    pulled = _item(client, name="Pulled Pork")
    assert client.get(f"/api/items/{pulled}").json()["base_unit"] == "each"

    client.post(
        "/api/recipes",
        json={"name": "Pulled Pork", "output_item_id": pulled, "output_quantity": 60, "output_unit": "oz"},
    )

    got = client.get(f"/api/items/{pulled}").json()
    assert got["base_unit"] == "g"
    assert got["stock_mode"] == "measured"


def test_a_counted_output_is_left_alone(client: httpx.Client) -> None:
    # "1 loaf" is a count: the default is already right and must not be rewritten.
    bread = _item(client, name="Bread")
    client.post(
        "/api/recipes",
        json={"name": "Simple Bread", "output_item_id": bread, "output_quantity": 1, "output_unit": "loaf"},
    )
    assert client.get(f"/api/items/{bread}").json()["base_unit"] == "each"


def test_a_stocked_output_item_is_never_re_expressed(client: httpx.Client) -> None:
    # Reinterpreting an existing balance in a new unit is the silent corruption
    # ADR 0016 made a one-time explicit migration. A stocked item keeps its unit.
    tortillas = _item(client, name="Tortillas")
    loc = _location(client, "Pantry")
    client.post(
        "/api/inventory/add",
        json={"item_id": tortillas, "location_id": loc, "quantity": 12, "unit": "each"},
    )
    client.post(
        "/api/recipes",
        json={"name": "Tortillas", "output_item_id": tortillas, "output_quantity": 500, "output_unit": "g"},
    )
    assert client.get(f"/api/items/{tortillas}").json()["base_unit"] == "each"
    assert _on_hand(client, loc, tortillas) == Decimal("12")


def test_an_unbridgeable_output_names_the_bridge_it_actually_needs(client: httpx.Client) -> None:
    # Density is g/ml and cannot bridge mass<->count at all; sending someone to
    # set one is advice that cannot fix their problem.
    jerky = _item(client, name="Jerky", size_quantity=6, size_unit="ea")  # counted, base 'each'
    beef = _item(client, name="Beef", size_quantity=2, size_unit="lb")
    loc = _location(client, "Pantry")
    client.post("/api/inventory/add", json={"item_id": beef, "location_id": loc, "quantity": 2, "unit": "lb"})
    recipe = client.post(
        "/api/recipes",
        json={
            "name": "Jerky",
            "output_item_id": jerky,
            "output_quantity": 8,
            "output_unit": "oz",
            "ingredients": [{"item_id": beef, "quantity_required": 1, "unit": "lb"}],
        },
    ).json()["id"]

    cook = client.post(f"/api/recipes/{recipe}/cook", json={"location_id": loc, "servings": 1})
    assert cook.status_code == 400
    detail = cook.json()["detail"]
    assert "weight-per-each" in detail and "density" not in detail


# --- measured yield beats declared yield (§3) -------------------------------


def test_the_weighed_dish_is_what_gets_stocked(client: httpx.Client) -> None:
    _pork, pulled, loc, recipe = _pulled_pork_setup(client)

    # The recipe says 60 oz. The scale says 4.5 lb. The scale wins, and the
    # servings multiplier is NOT applied on top of a measurement.
    cook = client.post(
        f"/api/recipes/{recipe}/cook",
        json={"location_id": loc, "servings": 1, "output_weight_grams": float(Decimal("4.5") * LB_G)},
    )
    assert cook.status_code == 200, cook.text
    assert _on_hand(client, loc, pulled) == pytest.approx(Decimal("2041.2"), abs=Decimal("0.5"))


def test_without_a_scale_the_declared_yield_still_stands(client: httpx.Client) -> None:
    _pork, pulled, loc, recipe = _pulled_pork_setup(client)
    cook = client.post(f"/api/recipes/{recipe}/cook", json={"location_id": loc, "servings": 1})
    assert cook.status_code == 200, cook.text
    # 60 oz, unchanged behaviour.
    assert _on_hand(client, loc, pulled) == pytest.approx(Decimal("1700.9"), abs=Decimal("0.5"))


def test_a_weight_beside_a_counted_yield_stays_metadata(client: httpx.Client) -> None:
    # "Two loaves, which together weigh 850 g" states two true things. The
    # loaves are what goes on the shelf; promoting the grams would store a
    # weight into a counted item and refuse the cook.
    flour = _item(client, name="Flour", size_quantity=5, size_unit="lb")
    bread = _item(client, name="Bread")
    loc = _location(client, "Kitchen")
    client.post("/api/inventory/add", json={"item_id": flour, "location_id": loc, "quantity": 5, "unit": "lb"})
    recipe = client.post(
        "/api/recipes",
        json={
            "name": "Simple Bread",
            "output_item_id": bread,
            "output_quantity": 1,
            "output_unit": "loaf",
            "ingredients": [{"item_id": flour, "quantity_required": 500, "unit": "g"}],
        },
    ).json()["id"]

    cook = client.post(
        f"/api/recipes/{recipe}/cook",
        json={"location_id": loc, "servings": 2, "output_weight_grams": 850},
    )
    assert cook.status_code == 200, cook.text
    assert Decimal(str(cook.json()["produced_quantity"])) == Decimal("2")
    assert _on_hand(client, loc, bread) == Decimal("2")


# --- derived yield: a ratio of what actually went in (§3) -------------------


def _ratio_recipe(client: httpx.Client, *, on_hand_lb: float, ratio: float = 0.6) -> tuple[str, str, str]:
    """Pulled pork whose yield follows the meat, not a fixed number.

    Stocks a little more than the line calls for. Stocking exactly the required
    amount fails by a third of a milligram — balances round to Numeric(12,3) on
    write while the required amount doesn't — which is the Phase 4 whole-package
    line's problem, not this test's.
    """
    pork = _item(client, name="Pork Butt Roast", size_quantity=4.87, size_unit="lb")
    pulled = _item(client, name="Pulled Pork")
    loc = _location(client, "Fridge")
    client.post(
        "/api/inventory/add",
        json={"item_id": pork, "location_id": loc, "quantity": on_hand_lb + 0.5, "unit": "lb"},
    )
    rub = _item(client, name="Dry Rub", size_quantity=8, size_unit="oz")
    client.post("/api/inventory/add", json={"item_id": rub, "location_id": loc, "quantity": 8, "unit": "oz"})
    recipe = client.post(
        "/api/recipes",
        json={
            "name": "Pulled Pork",
            "servings": 8,
            "output_item_id": pulled,
            "output_quantity": 60,
            "output_unit": "oz",
            "output_mode": "yield_ratio",
            "output_yield_ratio": ratio,
            "ingredients": [
                {"item_id": pork, "quantity_required": on_hand_lb, "unit": "lb", "yield_basis": True},
                {"item_id": rub, "quantity_required": 2, "unit": "oz"},
            ],
        },
    )
    assert recipe.status_code == 200, recipe.text
    return pulled, loc, recipe.json()["id"]


def test_the_yield_follows_the_meat_that_went_in(client: httpx.Client) -> None:
    # The same recipe against two different roasts must produce two different,
    # correct numbers — that is the whole point of a ratio.
    pulled, loc, recipe = _ratio_recipe(client, on_hand_lb=4.87)
    cook = client.post(f"/api/recipes/{recipe}/cook", json={"location_id": loc, "servings": 1})
    assert cook.status_code == 200, cook.text
    # 4.87 lb = 2209 g of meat × 0.6 = 1325.4 g.
    assert _on_hand(client, loc, pulled) == pytest.approx(Decimal("1325.4"), abs=Decimal("1"))


def test_a_bigger_roast_yields_more_from_the_same_recipe(client: httpx.Client) -> None:
    pulled, loc, recipe = _ratio_recipe(client, on_hand_lb=9)
    client.post(f"/api/recipes/{recipe}/cook", json={"location_id": loc, "servings": 1})
    # 9 lb = 4082 g × 0.6 = 2449.4 g — not the 1700 g the fixed yield would give.
    assert _on_hand(client, loc, pulled) == pytest.approx(Decimal("2449.4"), abs=Decimal("1"))


def test_the_rub_does_not_inflate_the_yield(client: httpx.Client) -> None:
    # Only the basis line counts. Were the ratio applied to every input, the
    # 2 oz of rub would add ~34 g of imaginary pulled pork.
    pulled, loc, recipe = _ratio_recipe(client, on_hand_lb=4.87)
    client.post(f"/api/recipes/{recipe}/cook", json={"location_id": loc, "servings": 1})
    meat_only = Decimal("4.87") * LB_G * Decimal("0.6")
    assert _on_hand(client, loc, pulled) == pytest.approx(meat_only, abs=Decimal("1"))


def test_a_weighed_dish_still_beats_the_ratio(client: httpx.Client) -> None:
    # Precedence: measured > derived > declared. The scale is the only one of
    # the three that observed the actual dish.
    pulled, loc, recipe = _ratio_recipe(client, on_hand_lb=4.87)
    client.post(
        f"/api/recipes/{recipe}/cook",
        json={"location_id": loc, "servings": 1, "output_weight_grams": 2000},
    )
    assert _on_hand(client, loc, pulled) == Decimal("2000")


def test_a_ratio_recipe_with_no_basis_line_is_refused(client: httpx.Client) -> None:
    # Asking for a ratio is asking for this number specifically, so an
    # uncomputable one refuses rather than quietly falling back to the declared
    # yield — a silently fixed number is what the ratio was meant to replace.
    pork = _item(client, name="Pork Butt Roast", size_quantity=4.87, size_unit="lb")
    pulled = _item(client, name="Pulled Pork")
    loc = _location(client, "Fridge")
    client.post("/api/inventory/add", json={"item_id": pork, "location_id": loc, "quantity": 5, "unit": "lb"})
    recipe = client.post(
        "/api/recipes",
        json={
            "name": "Pulled Pork",
            "servings": 8,
            "output_item_id": pulled,
            "output_quantity": 60,
            "output_unit": "oz",
            "output_mode": "yield_ratio",
            "output_yield_ratio": 0.6,
            "ingredients": [{"item_id": pork, "quantity_required": 3, "unit": "lb"}],
        },
    ).json()["id"]

    cook = client.post(f"/api/recipes/{recipe}/cook", json={"location_id": loc, "servings": 1})
    assert cook.status_code == 400
    assert "yield basis" in cook.json()["detail"]
    # Nothing was spent: the refusal happens before any removal (ADR 0017 §9).
    assert _on_hand(client, loc, pork) == pytest.approx(Decimal("2268"), abs=Decimal("1"))


def test_a_ratio_recipe_with_no_ratio_set_is_refused(client: httpx.Client) -> None:
    # Declared as a ratio recipe but never given a ratio — the one way to reach
    # this state, now that omitting the field on an edit preserves it.
    pork = _item(client, name="Pork Butt Roast", size_quantity=4.87, size_unit="lb")
    pulled = _item(client, name="Pulled Pork")
    loc = _location(client, "Fridge")
    client.post("/api/inventory/add", json={"item_id": pork, "location_id": loc, "quantity": 5, "unit": "lb"})
    recipe = client.post(
        "/api/recipes",
        json={
            "name": "Pulled Pork",
            "servings": 8,
            "output_item_id": pulled,
            "output_quantity": 60,
            "output_unit": "oz",
            "output_mode": "yield_ratio",
            "ingredients": [{"item_id": pork, "quantity_required": 3, "unit": "lb", "yield_basis": True}],
        },
    ).json()["id"]

    cook = client.post(f"/api/recipes/{recipe}/cook", json={"location_id": loc, "servings": 1})
    assert cook.status_code == 400
    assert "no ratio is set" in cook.json()["detail"]


def test_an_unrelated_edit_keeps_the_recipe_a_ratio_recipe(client: httpx.Client) -> None:
    # The editor's payload predates these fields, so a plain rename used to
    # demote a yield_ratio recipe to a fixed one and drop its ratio — an
    # unrelated edit silently changing what every future cook yields.
    pulled, loc, recipe = _ratio_recipe(client, on_hand_lb=4.87)
    renamed = client.put(
        f"/api/recipes/{recipe}",
        json={"name": "Pulled Pork (Sunday)", "servings": 8, "output_item_id": pulled,
              "output_quantity": 60, "output_unit": "oz"},
    )
    assert renamed.status_code == 200, renamed.text

    got = client.get(f"/api/recipes/{recipe}").json()["recipe"]
    assert got["output_mode"] == "yield_ratio"
    assert float(got["output_yield_ratio"]) == 0.6
    # And it still cooks like one.
    client.post(f"/api/recipes/{recipe}/cook", json={"location_id": loc, "servings": 1})
    assert _on_hand(client, loc, pulled) == pytest.approx(
        Decimal("4.87") * LB_G * Decimal("0.6"), abs=Decimal("1")
    )


def test_an_unrelated_edit_keeps_the_yield_basis_and_package_modes(client: httpx.Client) -> None:
    # Same class of bug one level down: PUT /ingredients rewrites every line, and
    # the editor submits only item/quantity/unit/optional.
    pork = _item(client, name="Pork Butt Roast", size_quantity=4.87, size_unit="lb", variable_weight=True)
    rub = _item(client, name="Dry Rub", size_quantity=8, size_unit="oz")
    pulled = _item(client, name="Pulled Pork")
    recipe = client.post(
        "/api/recipes",
        json={
            "name": "Pulled Pork", "servings": 8, "output_item_id": pulled,
            "output_quantity": 60, "output_unit": "oz",
            "ingredients": [
                {"item_id": pork, "quantity": 1, "unit": "each",
                 "quantity_mode": "package", "yield_basis": True},
                {"item_id": rub, "quantity": 2, "unit": "oz"},
            ],
        },
    ).json()["id"]

    resaved = client.put(
        f"/api/recipes/{recipe}/ingredients",
        json=[
            {"item_id": pork, "quantity_required": 1, "unit": "each"},
            {"item_id": rub, "quantity_required": 3, "unit": "oz"},
        ],
    )
    assert resaved.status_code == 200, resaved.text

    lines = {i["item_id"]: i for i in client.get(f"/api/recipes/{recipe}").json()["ingredients"]}
    assert lines[pork]["quantity_mode"] == "package"
    assert lines[pork]["yield_basis"] is True
    assert lines[rub]["quantity_mode"] == "amount"
    assert float(lines[rub]["quantity_required"]) == 3  # the edit itself still applied


def test_scaling_a_ratio_cook_scales_the_yield_with_it(client: httpx.Client) -> None:
    # Half a batch consumes half the meat, so it yields half the pork — without
    # the ratio ever being touched.
    pulled, loc, recipe = _ratio_recipe(client, on_hand_lb=4.87)
    client.post(
        f"/api/recipes/{recipe}/cook",
        json={"location_id": loc, "servings": 1, "servings_made": 4},
    )
    half = Decimal("4.87") * LB_G * Decimal("0.6") / 2
    assert _on_hand(client, loc, pulled) == pytest.approx(half, abs=Decimal("1"))


def test_a_fixed_recipe_is_untouched_by_any_of_this(client: httpx.Client) -> None:
    # 'fixed' is the default and the pre-existing behaviour: every recipe already
    # in the database keeps producing exactly what it produced before.
    _pork, pulled, loc, recipe = _pulled_pork_setup(client)
    assert client.get(f"/api/recipes/{recipe}").json()["recipe"]["output_mode"] == "fixed"
    client.post(f"/api/recipes/{recipe}/cook", json={"location_id": loc, "servings": 1})
    assert _on_hand(client, loc, pulled) == pytest.approx(Decimal("1700.9"), abs=Decimal("0.5"))


# --- the cook says what shape the yield was stored in (§4) ------------------


def test_the_yield_splits_evenly_into_the_tubs_asked_for(client: httpx.Client) -> None:
    _pork, pulled, loc, recipe = _pulled_pork_setup(client)
    cook = client.post(
        f"/api/recipes/{recipe}/cook",
        json={
            "location_id": loc,
            "servings": 1,
            "output_weight_grams": 1800,
            "output_portions": 3,
        },
    )
    assert cook.status_code == 200, cook.text
    body = cook.json()
    assert body["produced_portions"] == 3
    assert Decimal(str(body["produced_portion_size"])) == Decimal("600")

    lots = _lots(client, pulled)
    assert len(lots) == 1
    assert lots[0]["sealed_count"] == 3
    assert Decimal(str(lots[0]["container_size"])) == Decimal("600")


def test_an_uneven_split_does_not_leave_a_dust_lot(client: httpx.Client) -> None:
    # 1800/7 does not multiply back to 1800 in Decimal. Deriving the packed
    # amount from the divided size leaves a remainder of ~1e-27 g, which is not
    # food and must not become a second lot on the shelf.
    _pork, pulled, loc, recipe = _pulled_pork_setup(client)
    client.post(
        f"/api/recipes/{recipe}/cook",
        json={"location_id": loc, "servings": 1, "output_weight_grams": 1800, "output_portions": 7},
    )
    lots = _lots(client, pulled)
    assert len(lots) == 1
    assert lots[0]["sealed_count"] == 7
    assert _on_hand(client, loc, pulled) == Decimal("1800")


def test_finishing_one_tub_leaves_the_others(client: httpx.Client) -> None:
    # The whole point of packing tubs: each is finishable on its own. Without
    # containers, "I ate the leftovers" can only mean the entire batch.
    _pork, pulled, loc, recipe = _pulled_pork_setup(client)
    client.post(
        f"/api/recipes/{recipe}/cook",
        json={"location_id": loc, "servings": 1, "output_weight_grams": 1800, "output_portions": 3},
    )
    lot_id = _lots(client, pulled)[0]["lot_id"]

    finish = client.post(f"/api/inventory/lots/{lot_id}/consume", json={})
    assert finish.status_code == 200, finish.text

    lots = _lots(client, pulled)
    assert lots[0]["sealed_count"] == 2
    assert _on_hand(client, loc, pulled) == Decimal("1200")


def test_tubs_plus_what_stayed_in_the_serving_dish(client: httpx.Client) -> None:
    # The real evening: 4.5 lb made, three 1 lb tubs packed for the week, and the
    # rest still on the table. Two lots from one cook.
    _pork, pulled, loc, recipe = _pulled_pork_setup(client)
    cook = client.post(
        f"/api/recipes/{recipe}/cook",
        json={
            "location_id": loc,
            "servings": 1,
            "output_weight_grams": float(Decimal("4.5") * LB_G),
            "output_portions": 3,
            "output_portion_size": 1,
            "output_portion_unit": "lb",
        },
    )
    assert cook.status_code == 200, cook.text
    body = cook.json()
    assert body["produced_portions"] == 3
    assert Decimal(str(body["produced_loose"])) == pytest.approx(Decimal("680.4"), abs=Decimal("0.5"))

    lots = _lots(client, pulled)
    sealed = [lot for lot in lots if lot["sealed_count"]]
    loose = [lot for lot in lots if not lot["sealed_count"]]
    assert len(sealed) == 1 and len(loose) == 1
    assert Decimal(str(sealed[0]["container_size"])) == pytest.approx(LB_G, abs=Decimal("0.5"))


def test_dinner_comes_off_the_serving_dish_not_out_of_a_sealed_tub(client: httpx.Client) -> None:
    # With loose stock and sealed tubs side by side, eating must finish what's
    # already out before cracking one packed for later.
    _pork, pulled, loc, recipe = _pulled_pork_setup(client)
    client.post(
        f"/api/recipes/{recipe}/cook",
        json={
            "location_id": loc,
            "servings": 1,
            "output_weight_grams": 2400,
            "output_portions": 3,
            "output_portion_size": 600,
            "output_portion_unit": "g",
        },
    )
    # 600 g stayed loose; eating 400 g of it must not touch the three tubs.
    client.post(
        "/api/inventory/remove",
        json={"item_id": pulled, "location_id": loc, "quantity": 400, "unit": "g"},
    )

    lots = _lots(client, pulled)
    sealed = next(lot for lot in lots if lot["sealed_count"])
    loose = next(lot for lot in lots if not lot["sealed_count"])
    assert sealed["sealed_count"] == 3
    assert Decimal(str(loose["quantity_remaining"])) == Decimal("200")


def test_portioning_more_than_was_made_is_refused(client: httpx.Client) -> None:
    _pork, _pulled, loc, recipe = _pulled_pork_setup(client)
    cook = client.post(
        f"/api/recipes/{recipe}/cook",
        json={
            "location_id": loc,
            "servings": 1,
            "output_weight_grams": 900,
            "output_portions": 3,
            "output_portion_size": 500,
            "output_portion_unit": "g",
        },
    )
    assert cook.status_code == 400
    assert "more than" in cook.json()["detail"]


def test_no_portioning_asked_for_produces_one_plain_lot(client: httpx.Client) -> None:
    # Portioning is opt-in per cook — there is no recipe-level default, so a
    # cook that says nothing about tubs behaves exactly as it always did.
    _pork, pulled, loc, recipe = _pulled_pork_setup(client)
    cook = client.post(
        f"/api/recipes/{recipe}/cook",
        json={"location_id": loc, "servings": 1, "output_weight_grams": 1800},
    )
    assert cook.json().get("produced_portions") is None
    lots = _lots(client, pulled)
    assert len(lots) == 1
    assert lots[0]["container_size"] is None


# --- eating the output depletes it (§5) -------------------------------------


def _profile(client: httpx.Client) -> str:
    """A household profile to attribute the meal to (creating one is admin-gated)."""
    client.post("/api/auth/bootstrap-admin", json={"email": "admin@pantryos.local", "password": "TestPass123!"})
    login = client.post("/api/auth/login", json={"email": "admin@pantryos.local", "password": "TestPass123!"})
    headers = {"Authorization": f"Bearer {login.json()['token']}"}
    resp = client.post("/api/household/profiles", json={"display_name": "Casey"}, headers=headers)
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def test_eating_a_serving_takes_it_out_of_the_fridge(client: httpx.Client) -> None:
    # Cooked, eaten, and still on the books forever was the bug: plate-recipe
    # logged the calories against the output item with no location at all.
    _pork, pulled, loc, recipe = _pulled_pork_setup(client)
    profile = _profile(client)
    client.post(
        f"/api/recipes/{recipe}/cook",
        json={"location_id": loc, "servings": 1, "output_weight_grams": 1800},
    )
    assert _on_hand(client, loc, pulled) == Decimal("1800")

    # A serving is 60 oz / 8 = 7.5 oz ≈ 212.6 g; two of them ≈ 425.2 g.
    ate = client.post(
        "/api/consumption/plate-recipe",
        json={"recipe_id": recipe, "profile_id": profile, "servings": 2},
    )
    assert ate.status_code == 200, ate.text
    assert Decimal(str(ate.json()["deducted_quantity"])) == pytest.approx(Decimal("425.2"), abs=Decimal("0.5"))
    assert _on_hand(client, loc, pulled) == pytest.approx(Decimal("1374.8"), abs=Decimal("0.5"))


def test_eating_leftovers_opens_a_tub(client: httpx.Client) -> None:
    _pork, pulled, loc, recipe = _pulled_pork_setup(client)
    profile = _profile(client)
    client.post(
        f"/api/recipes/{recipe}/cook",
        json={"location_id": loc, "servings": 1, "output_weight_grams": 1800, "output_portions": 3},
    )
    client.post(
        "/api/consumption/plate-recipe",
        json={"recipe_id": recipe, "profile_id": profile, "servings": 1},
    )
    # One tub is opened and eaten into; the other two stay sealed.
    lots = _lots(client, pulled)
    assert sum(lot["sealed_count"] or 0 for lot in lots) == 2
    assert any(lot["status"] == "open" for lot in lots)


def test_eating_a_batch_split_across_locations_takes_from_both(client: httpx.Client) -> None:
    # 100 g in one place and 500 g in another must not cap a 300 g dinner at
    # 100 g — that logs the whole meal and leaves 200 g of already-eaten pork
    # on the books.
    pork, pulled, fridge, recipe = _pulled_pork_setup(client)
    freezer = _location(client, "Freezer")
    profile = _profile(client)
    # Each cook spends pork from its own location, so both need some.
    client.post("/api/inventory/add", json={"item_id": pork, "location_id": freezer, "quantity": 4.87, "unit": "lb"})
    for where, grams in ((fridge, 100), (freezer, 500)):
        cooked = client.post(
            f"/api/recipes/{recipe}/cook",
            json={"location_id": where, "servings": 1, "output_weight_grams": grams},
        )
        assert cooked.status_code == 200, cooked.text

    # A serving is 60 oz / 8 ≈ 212.6 g; ~1.41 servings is ~300 g.
    ate = client.post(
        "/api/consumption/plate-recipe",
        json={"recipe_id": recipe, "profile_id": profile, "servings": 1.411},
    )
    assert ate.status_code == 200, ate.text

    remaining = _on_hand(client, fridge, pulled) + _on_hand(client, freezer, pulled)
    assert remaining == pytest.approx(Decimal("300"), abs=Decimal("2"))
    assert Decimal(str(ate.json()["deducted_quantity"])) == pytest.approx(
        Decimal("300"), abs=Decimal("2")
    )


def test_a_meal_split_across_locations_is_one_entry_in_the_day(client: httpx.Client) -> None:
    # Each location needs its own event to decrement its own balance, but the
    # cook ate one dinner — it must not read as two, or double its calories.
    pork, pulled, fridge, recipe = _pulled_pork_setup(client)
    freezer = _location(client, "Freezer")
    profile = _profile(client)
    client.post("/api/inventory/add", json={"item_id": pork, "location_id": freezer, "quantity": 4.87, "unit": "lb"})
    for where, grams in ((fridge, 100), (freezer, 500)):
        client.post(
            f"/api/recipes/{recipe}/cook",
            json={"location_id": where, "servings": 1, "output_weight_grams": grams},
        )
    client.post(
        "/api/consumption/plate-recipe",
        json={"recipe_id": recipe, "profile_id": profile, "servings": 1.411},
    )

    day = client.get(f"/api/consumption/calories?profile_id={profile}").json()
    meals = [e for e in day["entries"] if e["item_name"] == "Pulled Pork"]
    assert len(meals) == 1
    assert float(meals[0]["quantity"]) == pytest.approx(1.411, abs=0.001)
    del pulled


def test_a_recipe_with_no_output_still_logs_without_touching_stock(client: httpx.Client) -> None:
    # Cooking already deducted the ingredients; a recipe that yields no tracked
    # item must not deduct anything a second time.
    flour = _item(client, name="Flour", size_quantity=5, size_unit="lb")
    loc = _location(client, "Kitchen")
    client.post("/api/inventory/add", json={"item_id": flour, "location_id": loc, "quantity": 5, "unit": "lb"})
    profile = _profile(client)
    recipe = client.post(
        "/api/recipes",
        json={
            "name": "Roux",
            "servings": 4,
            "ingredients": [{"item_id": flour, "quantity_required": 100, "unit": "g"}],
        },
    ).json()["id"]

    ate = client.post(
        "/api/consumption/plate-recipe",
        json={"recipe_id": recipe, "profile_id": profile, "servings": 1},
    )
    assert ate.status_code == 200, ate.text
    assert ate.json()["deducted_quantity"] is None
    assert _on_hand(client, loc, flour) == pytest.approx(Decimal("2268"), abs=Decimal("1"))


def test_eating_untracked_leftovers_is_still_loggable(client: httpx.Client) -> None:
    # Nothing stocked (the batch was cooked before any of this existed, or eaten
    # down to zero already) — the meal still logs, it just deducts nothing.
    _pork, _pulled, _loc, recipe = _pulled_pork_setup(client)
    profile = _profile(client)
    ate = client.post(
        "/api/consumption/plate-recipe",
        json={"recipe_id": recipe, "profile_id": profile, "servings": 1},
    )
    assert ate.status_code == 200, ate.text
    assert ate.json()["deducted_quantity"] is None
    assert ate.json()["location_id"] is None


def test_undoing_a_recipe_meal_puts_the_food_back(client: httpx.Client) -> None:
    _pork, pulled, loc, recipe = _pulled_pork_setup(client)
    profile = _profile(client)
    client.post(
        f"/api/recipes/{recipe}/cook",
        json={"location_id": loc, "servings": 1, "output_weight_grams": 1800},
    )
    ate = client.post(
        "/api/consumption/plate-recipe",
        json={"recipe_id": recipe, "profile_id": profile, "servings": 2},
    ).json()
    assert _on_hand(client, loc, pulled) < Decimal("1800")

    undo = client.delete(f"/api/consumption/plate/{ate['event_id']}")
    assert undo.status_code in (200, 204), undo.text
    assert _on_hand(client, loc, pulled) == pytest.approx(Decimal("1800"), abs=Decimal("0.5"))


def test_a_recipe_meal_cannot_be_edited_by_amount(client: httpx.Client) -> None:
    # Servings don't convert to grams of output, and `edit` re-logs in item
    # units. Refusing up front beats failing after the undo has committed.
    _pork, _pulled, loc, recipe = _pulled_pork_setup(client)
    profile = _profile(client)
    client.post(
        f"/api/recipes/{recipe}/cook",
        json={"location_id": loc, "servings": 1, "output_weight_grams": 1800},
    )
    ate = client.post(
        "/api/consumption/plate-recipe",
        json={"recipe_id": recipe, "profile_id": profile, "servings": 2},
    ).json()

    edited = client.patch(f"/api/consumption/plate/{ate['event_id']}", json={"quantity": 1})
    assert edited.status_code == 400
    assert "recipe meal" in edited.json()["detail"].lower()


def test_eating_more_than_is_stocked_never_goes_negative(client: httpx.Client) -> None:
    _pork, pulled, loc, recipe = _pulled_pork_setup(client)
    profile = _profile(client)
    client.post(
        f"/api/recipes/{recipe}/cook",
        json={"location_id": loc, "servings": 1, "output_weight_grams": 200},
    )
    client.post(
        "/api/consumption/plate-recipe",
        json={"recipe_id": recipe, "profile_id": profile, "servings": 8},
    )
    assert _on_hand(client, loc, pulled) == Decimal("0")
