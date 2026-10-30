"""Cooking "one roast", and sizing a batch from the pantry (ADR 0025 §5).

An amount line has to name a number, and for a variable-weight ingredient any
number it names is wrong for every package but one. Two ways out, and they solve
different halves of the problem:

  * a WHOLE-PACKAGE line — "one pork butt roast", resolved against the lot on the
    shelf, so the recipe never has to guess what a roast weighs;
  * SCALE-TO-INGREDIENT — "use all the pork I have", where the meat sizes the
    batch and the rub, the sauce and the yield all follow it.
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


def _stock_roast(client: httpx.Client, roast: str, loc: str, weight_lb: float) -> None:
    resp = client.post(
        "/api/inventory/add",
        json={"item_id": roast, "location_id": loc, "quantity": weight_lb, "unit": "lb"},
    )
    assert resp.status_code == 200, resp.text


def _package_recipe(client: httpx.Client, *, packages: float = 1) -> tuple[str, str, str, str]:
    """Pulled pork whose meat line means "one roast", not "3 lb of roast"."""
    roast = _item(client, name="Pork Butt Roast", size_quantity=4.87, size_unit="lb", variable_weight=True)
    pulled = _item(client, name="Pulled Pork")
    loc = _location(client, "Freezer")
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
            "ingredients": [
                {
                    "item_id": roast,
                    "quantity": packages,
                    "unit": "each",
                    "quantity_mode": "package",
                    "yield_basis": True,
                }
            ],
        },
    )
    assert recipe.status_code == 200, recipe.text
    return roast, pulled, loc, recipe.json()["id"]


# --- a whole-package line takes the package, whatever it weighs -------------


def test_one_package_spends_the_whole_roast(client: httpx.Client) -> None:
    roast, pulled, loc, recipe = _package_recipe(client)
    _stock_roast(client, roast, loc, 7.2)

    cook = client.post(f"/api/recipes/{recipe}/cook", json={"location_id": loc, "servings": 1})
    assert cook.status_code == 200, cook.text
    # The whole 7.2 lb roast went in — the recipe never had to name a weight.
    assert _on_hand(client, loc, roast) == Decimal("0")
    assert _on_hand(client, loc, pulled) == pytest.approx(
        Decimal("7.2") * LB_G * Decimal("0.6"), abs=Decimal("1")
    )


def test_the_same_recipe_takes_a_different_roast_whole(client: httpx.Client) -> None:
    # The point of the mode: two roasts, one recipe, no edit in between.
    roast, pulled, loc, recipe = _package_recipe(client)
    _stock_roast(client, roast, loc, 4.87)

    client.post(f"/api/recipes/{recipe}/cook", json={"location_id": loc, "servings": 1})
    assert _on_hand(client, loc, roast) == Decimal("0")
    assert _on_hand(client, loc, pulled) == pytest.approx(
        Decimal("4.87") * LB_G * Decimal("0.6"), abs=Decimal("1")
    )


def test_it_takes_the_first_expiring_package_and_leaves_the_rest(client: httpx.Client) -> None:
    roast, _pulled, loc, recipe = _package_recipe(client)
    _stock_roast(client, roast, loc, 4.87)
    _stock_roast(client, roast, loc, 7.2)

    client.post(f"/api/recipes/{recipe}/cook", json={"location_id": loc, "servings": 1})

    # One roast spent, one still there — not a fraction carved off each.
    lots = client.get(f"/api/inventory/lots?item_id={roast}").json()["lots"]
    assert len(lots) == 1
    assert Decimal(str(lots[0]["quantity_remaining"])) == pytest.approx(
        Decimal("7.2") * LB_G, abs=Decimal("0.5")
    )


def test_consuming_a_container_does_not_crack_the_next_one(client: httpx.Client) -> None:
    # Quantities persist at Numeric(12,3); a container size travels in the event
    # PAYLOAD, where no column rounds it. Unrounded, the lot claimed a container
    # a ten-thousandth of a gram larger than the quantity backing it, and
    # consuming the reported amount overdrew into the next sealed container —
    # "use one roast" quietly opened two. 4.87 lb is 2208.9948419 g, so this
    # reproduces whenever a conversion isn't exact.
    roast = _item(client, name="Pork Butt Roast", size_quantity=4.87, size_unit="lb", variable_weight=True)
    loc = _location(client, "Freezer")
    _stock_roast(client, roast, loc, 4.87)
    _stock_roast(client, roast, loc, 7.2)

    lots = client.get(f"/api/inventory/lots?item_id={roast}").json()["lots"]
    first = min(lots, key=lambda lot: Decimal(str(lot["quantity_remaining"])))
    client.post(
        "/api/inventory/remove",
        json={
            "item_id": roast,
            "location_id": loc,
            "quantity": float(first["quantity_remaining"]),
            "unit": first["unit"],
        },
    )

    after = client.get(f"/api/inventory/lots?item_id={roast}").json()["lots"]
    assert len(after) == 1
    assert after[0]["status"] == "sealed"
    assert after[0]["sealed_count"] == 1


def test_two_packages_take_two_whole_roasts(client: httpx.Client) -> None:
    roast, _pulled, loc, recipe = _package_recipe(client, packages=2)
    _stock_roast(client, roast, loc, 4.87)
    _stock_roast(client, roast, loc, 7.2)

    cook = client.post(f"/api/recipes/{recipe}/cook", json={"location_id": loc, "servings": 1})
    assert cook.status_code == 200, cook.text
    assert _on_hand(client, loc, roast) == Decimal("0")


def test_asking_for_more_packages_than_exist_is_refused(client: httpx.Client) -> None:
    roast, _pulled, loc, recipe = _package_recipe(client, packages=2)
    _stock_roast(client, roast, loc, 4.87)

    cook = client.post(f"/api/recipes/{recipe}/cook", json={"location_id": loc, "servings": 1})
    assert cook.status_code == 400
    assert "short 1 package" in cook.json()["detail"]
    # Nothing spent — the refusal is in planning (ADR 0017 §9).
    assert _on_hand(client, loc, roast) == pytest.approx(Decimal("4.87") * LB_G, abs=Decimal("0.5"))


def test_availability_counts_packages_not_grams(client: httpx.Client) -> None:
    # Left on the amount path this line would report on-hand by converting grams
    # through the item's NOMINAL package size — the fiction this mode exists to
    # avoid. It has to read "1 package".
    roast, _pulled, loc, recipe = _package_recipe(client)
    _stock_roast(client, roast, loc, 7.2)

    rows = client.get(f"/api/recipes/{recipe}/availability").json()["ingredients"]
    line = rows[0]
    assert line["unit"] == "package"
    assert line["status"] == "have"
    assert Decimal(line["on_hand"]) == Decimal("1")


def test_a_per_line_override_opts_back_out(client: httpx.Client) -> None:
    # Typing an amount means you meant that amount — half the roast, not all of it.
    roast, _pulled, loc, recipe = _package_recipe(client)
    _stock_roast(client, roast, loc, 7.2)
    ingredient_id = client.get(f"/api/recipes/{recipe}/availability").json()["ingredients"][0][
        "ingredient_id"
    ]

    cook = client.post(
        f"/api/recipes/{recipe}/cook",
        json={
            "location_id": loc,
            "servings": 1,
            "ingredient_overrides": [{"ingredient_id": ingredient_id, "quantity": 2, "unit": "lb"}],
        },
    )
    assert cook.status_code == 200, cook.text
    assert _on_hand(client, loc, roast) == pytest.approx(
        Decimal("5.2") * LB_G, abs=Decimal("1")
    )


# --- sizing the batch from the pantry ---------------------------------------


def _amount_recipe(client: httpx.Client) -> tuple[str, str, str, str, str]:
    """The same dish written the ordinary way: 3 lb of pork and 2 oz of rub."""
    roast = _item(client, name="Pork Butt Roast", size_quantity=4.87, size_unit="lb", variable_weight=True)
    rub = _item(client, name="Dry Rub", size_quantity=16, size_unit="oz")
    pulled = _item(client, name="Pulled Pork")
    loc = _location(client, "Freezer")
    client.post("/api/inventory/add", json={"item_id": rub, "location_id": loc, "quantity": 16, "unit": "oz"})
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
            "ingredients": [
                {"item_id": roast, "quantity": 3, "unit": "lb", "yield_basis": True},
                {"item_id": rub, "quantity": 2, "unit": "oz"},
            ],
        },
    ).json()["id"]
    return roast, rub, pulled, loc, recipe


def _pork_line(client: httpx.Client, recipe: str, roast: str) -> str:
    rows = client.get(f"/api/recipes/{recipe}/availability").json()["ingredients"]
    return next(r["ingredient_id"] for r in rows if r["item_id"] == roast)


def test_using_all_the_pork_scales_the_whole_recipe(client: httpx.Client) -> None:
    roast, rub, pulled, loc, recipe = _amount_recipe(client)
    _stock_roast(client, roast, loc, 4.87)

    cook = client.post(
        f"/api/recipes/{recipe}/cook",
        json={"location_id": loc, "scale_to_ingredient_id": _pork_line(client, recipe, roast)},
    )
    assert cook.status_code == 200, cook.text

    # 4.87 lb against a recipe wanting 3 lb → a 1.623× batch.
    assert float(cook.json()["scale_used"]) == pytest.approx(4.87 / 3, abs=0.001)
    # All the pork is gone...
    assert _on_hand(client, loc, roast) == Decimal("0")
    # ...the rub scaled with it (16 oz - 2 oz × 1.623)...
    rub_used = Decimal("2") * (Decimal("4.87") / Decimal("3")) * Decimal("28.349523125")
    assert _on_hand(client, loc, rub) == pytest.approx(
        Decimal("16") * Decimal("28.349523125") - rub_used, abs=Decimal("1")
    )
    # ...and the yield followed the meat that actually went in.
    assert _on_hand(client, loc, pulled) == pytest.approx(
        Decimal("4.87") * LB_G * Decimal("0.6"), abs=Decimal("1")
    )


def test_exactly_enough_stock_is_not_a_rounding_shortfall(client: httpx.Client) -> None:
    # Balances round to Numeric(12,3) on write; a required amount doesn't. Asking
    # for "9 lb" against a lot holding exactly 9 lb is short by a third of a
    # milligram. Sizing the cook from the pantry asks for what is there.
    roast, _rub, _pulled, loc, recipe = _amount_recipe(client)
    _stock_roast(client, roast, loc, 9)

    cook = client.post(
        f"/api/recipes/{recipe}/cook",
        json={"location_id": loc, "scale_to_ingredient_id": _pork_line(client, recipe, roast)},
    )
    assert cook.status_code == 200, cook.text
    assert _on_hand(client, loc, roast) == Decimal("0")


def test_scaling_from_an_ingredient_with_none_on_hand_is_refused(client: httpx.Client) -> None:
    roast, _rub, _pulled, loc, recipe = _amount_recipe(client)

    cook = client.post(
        f"/api/recipes/{recipe}/cook",
        json={"location_id": loc, "scale_to_ingredient_id": _pork_line(client, recipe, roast)},
    )
    assert cook.status_code == 400
    assert "there is none on hand" in cook.json()["detail"]


def test_scaling_from_a_package_line_is_refused(client: httpx.Client) -> None:
    # A package line already means "whatever the package holds" — there is no
    # amount to scale it to, and pretending otherwise would invent one.
    roast, _pulled, loc, recipe = _package_recipe(client)
    _stock_roast(client, roast, loc, 4.87)
    line = client.get(f"/api/recipes/{recipe}/availability").json()["ingredients"][0]["ingredient_id"]

    cook = client.post(
        f"/api/recipes/{recipe}/cook",
        json={"location_id": loc, "scale_to_ingredient_id": line},
    )
    assert cook.status_code == 400
    assert "already means" in cook.json()["detail"]


def test_scaling_from_a_line_of_another_recipe_is_refused(client: httpx.Client) -> None:
    roast, _rub, _pulled, loc, recipe = _amount_recipe(client)
    _stock_roast(client, roast, loc, 4.87)
    other = client.post(
        "/api/recipes",
        json={
            "name": "Something Else",
            "ingredients": [{"item_id": roast, "quantity": 1, "unit": "lb"}],
        },
    ).json()["id"]
    stray = client.get(f"/api/recipes/{other}/availability").json()["ingredients"][0]["ingredient_id"]

    cook = client.post(
        f"/api/recipes/{recipe}/cook",
        json={"location_id": loc, "scale_to_ingredient_id": stray},
    )
    assert cook.status_code == 404


def test_an_ordinary_cook_is_untouched(client: httpx.Client) -> None:
    # Both features are opt-in; with neither, nothing about the cook changes.
    roast, _rub, _pulled, loc, recipe = _amount_recipe(client)
    _stock_roast(client, roast, loc, 5)

    cook = client.post(f"/api/recipes/{recipe}/cook", json={"location_id": loc, "servings": 1})
    assert cook.status_code == 200, cook.text
    assert float(cook.json()["scale_used"]) == 1.0
    assert _on_hand(client, loc, roast) == pytest.approx(
        Decimal("2") * LB_G, abs=Decimal("1")
    )
