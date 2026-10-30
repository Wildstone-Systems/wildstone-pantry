"""One UPC, a different weight in every package (ADR 0025 §1).

A pork butt roast scans the same barcode whether it weighs 4.87 lb or 9 lb.
`items.size_quantity` is a class-level fact and was being used as a per-package
one, so every "1 each" check-in booked the catalogue's nominal instead of the
meat actually in hand — and the container form of the check-in, which does
capture the true weight, wrote it back onto the item, making the nominal mean
"whatever was scanned last".
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


def _item(client: httpx.Client, **kw) -> dict:
    resp = client.post("/api/items", json=kw)
    assert resp.status_code == 201, resp.text
    return resp.json()


def _location(client: httpx.Client, name: str) -> str:
    return client.post("/api/locations", json={"name": name}).json()["id"]


def _on_hand(client: httpx.Client, location_id: str, item_id: str) -> Decimal:
    rows = client.get(f"/api/inventory?location_id={location_id}").json()["items"]
    match = next((r for r in rows if r["item_id"] == item_id), None)
    return Decimal(str(match["quantity"])) if match else Decimal("0")


def _lots(client: httpx.Client, item_id: str) -> list[dict]:
    return client.get(f"/api/inventory/lots?item_id={item_id}").json()["lots"]


def _roast(client: httpx.Client) -> dict:
    return _item(client, name="Pork Butt Roast", size_quantity=4.87, size_unit="lb", variable_weight=True)


# --- what the flag means ----------------------------------------------------


def test_a_variable_weight_item_is_measured_by_weight(client: httpx.Client) -> None:
    # Calling a product variable-weight IS saying it is weighed: one barcode over
    # differently-sized packages only makes sense for something on a scale.
    # Without this a size-less roast defaults to counted/'each' and the first
    # check-in has nowhere honest to put 4.87 lb.
    roast = _item(client, name="Pork Butt Roast", variable_weight=True)
    assert roast["variable_weight"] is True
    assert roast["stock_mode"] == "measured"
    assert roast["base_unit"] == "g"


def test_an_ordinary_item_is_unaffected(client: httpx.Client) -> None:
    plain = _item(client, name="Canned Beans", size_quantity=15, size_unit="oz")
    assert plain["variable_weight"] is False
    assert plain["base_unit"] == "g"


# --- intake asks for the weight of the package in hand ----------------------


def test_checking_one_in_by_the_each_is_refused(client: httpx.Client) -> None:
    # This is the original bug: "1 each" silently booked the nominal 4.87 lb no
    # matter what was in the bag.
    roast = _roast(client)
    loc = _location(client, "Freezer")
    resp = client.post(
        "/api/inventory/add",
        json={"item_id": roast["id"], "location_id": loc, "quantity": 1, "unit": "each"},
    )
    assert resp.status_code == 400
    detail = resp.json()["detail"]
    assert "weighs something different in every package" in detail
    assert "4.87 lb" in detail  # the nominal is offered as the starting point
    assert _on_hand(client, loc, roast["id"]) == Decimal("0")


def test_checking_one_in_by_weight_books_that_weight(client: httpx.Client) -> None:
    roast = _roast(client)
    loc = _location(client, "Freezer")
    resp = client.post(
        "/api/inventory/add",
        json={"item_id": roast["id"], "location_id": loc, "quantity": 7.2, "unit": "lb"},
    )
    assert resp.status_code == 200, resp.text
    # 7.2 lb, not the 4.87 lb nominal.
    assert _on_hand(client, loc, roast["id"]) == pytest.approx(Decimal("7.2") * LB_G, abs=Decimal("0.5"))


def test_two_different_roasts_keep_their_own_weights(client: httpx.Client) -> None:
    # The heart of it: same item, same barcode, two lots that disagree — and the
    # total is the sum of what was actually bought.
    roast = _roast(client)
    loc = _location(client, "Freezer")
    for weight in (4.87, 7.2):
        client.post(
            "/api/inventory/add",
            json={"item_id": roast["id"], "location_id": loc, "quantity": weight, "unit": "lb"},
        )

    lots = _lots(client, roast["id"])
    assert len(lots) == 2
    sizes = sorted(Decimal(str(lot["container_size"])) for lot in lots)
    assert sizes[0] == pytest.approx(Decimal("4.87") * LB_G, abs=Decimal("0.5"))
    assert sizes[1] == pytest.approx(Decimal("7.2") * LB_G, abs=Decimal("0.5"))
    assert _on_hand(client, loc, roast["id"]) == pytest.approx(
        Decimal("12.07") * LB_G, abs=Decimal("1")
    )


def test_each_check_in_is_one_countable_package(client: httpx.Client) -> None:
    # "How many roasts do I have" and "how many grams" are both answerable —
    # storing by weight alone would lose the first, counting alone loses the second.
    roast = _roast(client)
    loc = _location(client, "Freezer")
    for weight in (4.87, 7.2, 5.5):
        client.post(
            "/api/inventory/add",
            json={"item_id": roast["id"], "location_id": loc, "quantity": weight, "unit": "lb"},
        )
    lots = _lots(client, roast["id"])
    assert sum(lot["sealed_count"] or 0 for lot in lots) == 3


def test_the_nominal_size_is_never_overwritten(client: httpx.Client) -> None:
    # The write-back is deliberate and tested for ordinary items (a scan
    # prefilling the next one). For catch weight it would make the catalogue's
    # size mean "whatever was scanned last" and re-rule every earlier lot.
    roast = _roast(client)
    loc = _location(client, "Freezer")
    client.post(
        "/api/inventory/add",
        json={
            "item_id": roast["id"], "location_id": loc, "quantity": 1,
            "container_size": 9, "container_unit": "lb", "sealed_count": 1,
        },
    )
    got = client.get(f"/api/items/{roast['id']}").json()
    assert float(got["size_quantity"]) == 4.87
    assert got["size_unit"] == "lb"
    # ...but the lot knows the real weight.
    assert Decimal(str(_lots(client, roast["id"])[0]["container_size"])) == pytest.approx(
        Decimal("9") * LB_G, abs=Decimal("0.5")
    )


def test_an_ordinary_item_still_learns_its_size_from_a_check_in(client: httpx.Client) -> None:
    # The gate is the flag, not the container form — ADR 0014's prefill behaviour
    # has to survive intact for everything that isn't catch weight.
    milk = _item(client, name="Whole Milk", stock_mode="measured", size_quantity=1, size_unit="gal")
    loc = _location(client, "Fridge")
    client.post(
        "/api/inventory/add",
        json={
            "item_id": milk["id"], "location_id": loc, "quantity": 1,
            "container_size": 0.5, "container_unit": "gal", "sealed_count": 2,
        },
    )
    got = client.get(f"/api/items/{milk['id']}").json()
    assert float(got["size_quantity"]) == 0.5


# --- and it works through a recipe ------------------------------------------


def test_a_recipe_spends_the_roast_that_is_actually_there(client: httpx.Client) -> None:
    roast = _roast(client)
    pulled = _item(client, name="Pulled Pork")
    loc = _location(client, "Freezer")
    client.post(
        "/api/inventory/add",
        json={"item_id": roast["id"], "location_id": loc, "quantity": 7.2, "unit": "lb"},
    )
    recipe = client.post(
        "/api/recipes",
        json={
            "name": "Pulled Pork",
            "servings": 8,
            "output_item_id": pulled["id"],
            "output_quantity": 60,
            "output_unit": "oz",
            "output_mode": "yield_ratio",
            "output_yield_ratio": 0.6,
            "ingredients": [
                {"item_id": roast["id"], "quantity_required": 7, "unit": "lb", "yield_basis": True}
            ],
        },
    ).json()["id"]

    cook = client.post(f"/api/recipes/{recipe}/cook", json={"location_id": loc, "servings": 1})
    assert cook.status_code == 200, cook.text
    # 7 lb of meat × 0.6 — derived from the real roast, not from any nominal.
    assert _on_hand(client, loc, pulled["id"]) == pytest.approx(
        Decimal("7") * LB_G * Decimal("0.6"), abs=Decimal("1")
    )


def test_turning_the_flag_on_later_leaves_stocked_balances_alone(client: httpx.Client) -> None:
    # Re-expressing a live balance in a new unit is an explicit migration, never
    # a side effect of ticking a checkbox (ADR 0016).
    beef = _item(client, name="Ground Beef")
    loc = _location(client, "Freezer")
    client.post(
        "/api/inventory/add",
        json={"item_id": beef["id"], "location_id": loc, "quantity": 3, "unit": "each"},
    )
    client.put(f"/api/items/{beef['id']}", json={"variable_weight": True})

    got = client.get(f"/api/items/{beef['id']}").json()
    assert got["variable_weight"] is True
    assert got["base_unit"] == "each"  # unchanged — there is stock counted in it
    assert _on_hand(client, loc, beef["id"]) == Decimal("3")
