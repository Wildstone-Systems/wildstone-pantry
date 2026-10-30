"""Cooking with what's actually on the shelf (ADR 0025 §7).

Sizing a batch to a 4.87 lb roast asks for 1.62× the rub, and the jar holds one
batch's worth. Refusing the cook over that is technically correct and useless:
the real answer is to season it a little lighter and get on with it.

What must NOT happen is doing that quietly. A dish made with 60% of its rub is
not the dish that was written down, so every trimmed line is reported back.
"""

from __future__ import annotations

from decimal import Decimal

import httpx
import pytest

from app.core.config import settings

LB_G = Decimal("453.59237")
OZ_G = Decimal("28.349523125")


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


def _setup(client: httpx.Client, *, pork_lb: float = 4.87, rub_oz: float = 2) -> dict:
    """Pulled pork: 3 lb of meat and 2 oz of rub, against a pantry you choose."""
    pork = _item(client, name="Pork Butt Roast", size_quantity=4.87, size_unit="lb", variable_weight=True)
    rub = _item(client, name="Dry Rub", size_quantity=8, size_unit="oz")
    pulled = _item(client, name="Pulled Pork")
    loc = _location(client, "Kitchen")
    client.post("/api/inventory/add", json={"item_id": pork, "location_id": loc, "quantity": pork_lb, "unit": "lb"})
    if rub_oz > 0:
        client.post("/api/inventory/add", json={"item_id": rub, "location_id": loc, "quantity": rub_oz, "unit": "oz"})
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
                {"item_id": pork, "quantity": 3, "unit": "lb", "yield_basis": True},
                {"item_id": rub, "quantity": 2, "unit": "oz"},
            ],
        },
    )
    assert recipe.status_code == 200, recipe.text
    recipe_id = recipe.json()["id"]
    rows = client.get(f"/api/recipes/{recipe_id}/availability").json()["ingredients"]
    return {
        "pork": pork,
        "rub": rub,
        "pulled": pulled,
        "loc": loc,
        "recipe": recipe_id,
        "pork_line": next(r["ingredient_id"] for r in rows if r["item_id"] == pork),
    }


def test_without_the_flag_a_short_line_still_refuses(client: httpx.Client) -> None:
    # The default is unchanged: all-or-nothing, nothing spent.
    s = _setup(client, pork_lb=4.87, rub_oz=2)

    cook = client.post(
        f"/api/recipes/{s['recipe']}/cook",
        json={"location_id": s["loc"], "scale_to_ingredient_id": s["pork_line"]},
    )
    assert cook.status_code == 400
    assert "Insufficient inventory" in cook.json()["detail"]
    assert _on_hand(client, s["loc"], s["pork"]) > 0
    assert _on_hand(client, s["loc"], s["rub"]) > 0


def test_capping_uses_everything_there_is(client: httpx.Client) -> None:
    # The whole point: all 4.87 lb of pork goes in, and so does the whole 2 oz
    # jar of rub even though a 1.62× batch calls for 3.25 oz.
    s = _setup(client, pork_lb=4.87, rub_oz=2)

    cook = client.post(
        f"/api/recipes/{s['recipe']}/cook",
        json={
            "location_id": s["loc"],
            "scale_to_ingredient_id": s["pork_line"],
            "allow_short_ingredients": True,
        },
    )
    assert cook.status_code == 200, cook.text
    assert _on_hand(client, s["loc"], s["pork"]) == Decimal("0")
    assert _on_hand(client, s["loc"], s["rub"]) == Decimal("0")


def test_the_trimmed_line_is_reported_not_absorbed(client: httpx.Client) -> None:
    # A dish made with 62% of its rub is a different dish. Saying so is the
    # difference between a feature and a silent wrong number.
    s = _setup(client, pork_lb=4.87, rub_oz=2)

    body = client.post(
        f"/api/recipes/{s['recipe']}/cook",
        json={
            "location_id": s["loc"],
            "scale_to_ingredient_id": s["pork_line"],
            "allow_short_ingredients": True,
        },
    ).json()

    capped = body["capped_ingredients"]
    assert len(capped) == 1
    assert capped[0]["item_name"] == "Dry Rub"
    # Wanted 2 oz × 1.62 ≈ 92 g, took the 56.7 g that were there.
    assert Decimal(str(capped[0]["wanted"])) == pytest.approx(
        Decimal("2") * (Decimal("4.87") / Decimal("3")) * OZ_G, abs=Decimal("1")
    )
    assert Decimal(str(capped[0]["took"])) == pytest.approx(Decimal("2") * OZ_G, abs=Decimal("0.5"))
    assert capped[0]["unit"] == "g"


def test_a_cook_that_needed_no_trimming_reports_nothing(client: httpx.Client) -> None:
    # The flag is permission, not an instruction — with enough rub, nothing is
    # capped and the response says so.
    s = _setup(client, pork_lb=4.87, rub_oz=8)

    body = client.post(
        f"/api/recipes/{s['recipe']}/cook",
        json={
            "location_id": s["loc"],
            "scale_to_ingredient_id": s["pork_line"],
            "allow_short_ingredients": True,
        },
    ).json()
    assert body["capped_ingredients"] == []
    # 8 oz of rub, 3.25 of them used.
    assert _on_hand(client, s["loc"], s["rub"]) > 0


def test_the_yield_still_follows_the_meat(client: httpx.Client) -> None:
    # Capping the rub must not disturb the yield: it is keyed to the basis line,
    # which got everything it asked for.
    s = _setup(client, pork_lb=4.87, rub_oz=2)

    client.post(
        f"/api/recipes/{s['recipe']}/cook",
        json={
            "location_id": s["loc"],
            "scale_to_ingredient_id": s["pork_line"],
            "allow_short_ingredients": True,
        },
    )
    assert _on_hand(client, s["loc"], s["pulled"]) == pytest.approx(
        Decimal("4.87") * LB_G * Decimal("0.6"), abs=Decimal("1")
    )


def test_a_line_with_nothing_on_hand_is_capped_to_zero_and_named(client: httpx.Client) -> None:
    # Not a silent omission — "took 0 of 92 g" is the loudest thing the response
    # can say, and the availability screen already flags it as missing up front.
    s = _setup(client, pork_lb=4.87, rub_oz=0)

    body = client.post(
        f"/api/recipes/{s['recipe']}/cook",
        json={
            "location_id": s["loc"],
            "scale_to_ingredient_id": s["pork_line"],
            "allow_short_ingredients": True,
        },
    ).json()
    capped = body["capped_ingredients"]
    assert len(capped) == 1
    assert Decimal(str(capped[0]["took"])) == Decimal("0")


def test_an_unmeasurable_line_still_refuses(client: httpx.Client) -> None:
    # "Less of it" and "we can't tell how much" are different problems, and only
    # the first is a decision a cook can knowingly make. Capping an amount we
    # were never able to measure would be inventing the number.
    mystery = _item(client, name="Mystery Spice")  # counted, no size → no bridge
    loc = _location(client, "Pantry")
    client.post("/api/inventory/add", json={"item_id": mystery, "location_id": loc, "quantity": 3, "unit": "each"})
    recipe = client.post(
        "/api/recipes",
        json={
            "name": "Mystery Dish",
            "ingredients": [{"item_id": mystery, "quantity": 2, "unit": "tbsp"}],
        },
    ).json()["id"]

    cook = client.post(
        f"/api/recipes/{recipe}/cook",
        json={"location_id": loc, "servings": 1, "allow_short_ingredients": True},
    )
    assert cook.status_code == 400
    assert "Can't convert units" in cook.json()["detail"]
    assert _on_hand(client, loc, mystery) == Decimal("3")


def test_capping_works_without_scaling_too(client: httpx.Client) -> None:
    # The two are independent: a plain 2× batch against a half-empty pantry can
    # cap just as usefully.
    s = _setup(client, pork_lb=10, rub_oz=2)

    body = client.post(
        f"/api/recipes/{s['recipe']}/cook",
        json={"location_id": s["loc"], "servings": 2, "allow_short_ingredients": True},
    )
    assert body.status_code == 200, body.text
    assert [c["item_name"] for c in body.json()["capped_ingredients"]] == ["Dry Rub"]
