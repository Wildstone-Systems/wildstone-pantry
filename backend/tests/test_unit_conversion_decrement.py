"""Measured items store and consume in a base unit (ml/g), not "1 each" (ADR 0016).

The original bug: 2% milk tracked as 1 'each' (a gallon); removing "1 cup" zeroed
it. Now a measured item's on-hand is its net contents in a base unit — a gallon
jug is 3785 ml — so any consume unit (cup, g) converts and decrements
proportionally, using the item's size + density, and never guesses when it can't.
"""

from __future__ import annotations

from decimal import Decimal

import httpx
import pytest

from app.core.config import settings
from modules.catalog.services.units import convert_quantity


@pytest.fixture(autouse=True)
def _personal_mode(monkeypatch) -> None:
    monkeypatch.setattr(settings, "auth_enabled", False)


# --- pure converter ---------------------------------------------------------

def test_same_dimension_needs_no_hints() -> None:
    assert convert_quantity(Decimal("1"), "gal", "cup") == pytest.approx(Decimal("16"), abs=Decimal("0.01"))
    assert convert_quantity(Decimal("1"), "kg", "g") == Decimal("1000")


def test_count_to_volume_uses_ml_per_each() -> None:
    # 1 cup out of a 1-gallon 'each' → 1/16 each.
    each = convert_quantity(Decimal("1"), "cup", "each", ml_per_each=Decimal("3785.41"))
    assert each == pytest.approx(Decimal("0.0625"), abs=Decimal("0.0005"))


def test_volume_to_mass_uses_density() -> None:
    assert convert_quantity(Decimal("1"), "cup", "g", density_g_per_ml=Decimal("1.03")) == pytest.approx(
        Decimal("243.7"), abs=Decimal("0.5")
    )


def test_returns_none_when_a_bridge_is_missing() -> None:
    # cup → each with no size/density is unknowable; never guess.
    assert convert_quantity(Decimal("1"), "cup", "each") is None


# --- end to end -------------------------------------------------------------

def _seed_milk(client: httpx.Client) -> tuple[str, str]:
    item = client.post(
        "/api/items",
        json={"name": "2% Milk", "size_quantity": 1, "size_unit": "gal", "density_g_per_ml": 1.03},
    )
    assert item.status_code == 201, item.text
    loc = client.post("/api/locations", json={"name": "Fridge"})
    assert loc.status_code == 201, loc.text
    return item.json()["id"], loc.json()["id"]


def _qty_on_hand(client: httpx.Client, location_id: str, item_id: str) -> Decimal | None:
    rows = client.get(f"/api/inventory?location_id={location_id}").json()["items"]
    match = next((r for r in rows if r["item_id"] == item_id), None)
    return Decimal(str(match["quantity"])) if match else None


def test_adding_a_jug_stores_its_contents_in_the_base_unit(client: httpx.Client) -> None:
    # A measured item (gal size) has base unit ml (ADR 0016). Buying 1 jug stores
    # its net contents — a gallon = 3785.41 ml — not "1 each".
    item_id, loc_id = _seed_milk(client)
    add = client.post(
        "/api/inventory/add",
        json={"item_id": item_id, "location_id": loc_id, "quantity": 1, "unit": "each"},
    )
    assert add.status_code in {200, 201}, add.text
    assert _qty_on_hand(client, loc_id, item_id) == pytest.approx(Decimal("3785.41"), abs=Decimal("0.5"))


def test_removing_one_cup_decrements_proportionally_not_wholesale(client: httpx.Client) -> None:
    item_id, loc_id = _seed_milk(client)
    client.post("/api/inventory/add", json={"item_id": item_id, "location_id": loc_id, "quantity": 1, "unit": "each"})

    resp = client.post(
        "/api/inventory/remove",
        json={"item_id": item_id, "location_id": loc_id, "quantity": 1, "unit": "cup"},
    )
    assert resp.status_code == 200, resp.text
    remaining = _qty_on_hand(client, loc_id, item_id)
    # 3785.41 ml - 1 cup (236.588 ml) = 3548.82 ml; a cup out of a gallon, not zeroed.
    assert remaining is not None
    assert remaining == pytest.approx(Decimal("3548.82"), abs=Decimal("0.5"))


def test_removing_a_whole_jug_by_each(client: httpx.Client) -> None:
    # "1 each" of a measured item = one package = its net contents in base.
    item_id, loc_id = _seed_milk(client)
    client.post("/api/inventory/add", json={"item_id": item_id, "location_id": loc_id, "quantity": 2, "unit": "each"})

    resp = client.post(
        "/api/inventory/remove",
        json={"item_id": item_id, "location_id": loc_id, "quantity": 1, "unit": "each"},
    )
    assert resp.status_code == 200, resp.text
    # 2 gal (7570.82 ml) - 1 jug (3785.41 ml) = 3785.41 ml.
    assert _qty_on_hand(client, loc_id, item_id) == pytest.approx(Decimal("3785.41"), abs=Decimal("0.5"))


def test_unconvertible_removal_is_refused_not_mis_decremented(client: httpx.Client) -> None:
    # A counted item with no size/density: cup → each is unknowable.
    item = client.post("/api/items", json={"name": "Mystery Box"}).json()
    loc = client.post("/api/locations", json={"name": "Shelf"}).json()
    client.post("/api/inventory/add", json={"item_id": item["id"], "location_id": loc["id"], "quantity": 3, "unit": "each"})

    resp = client.post(
        "/api/inventory/remove",
        json={"item_id": item["id"], "location_id": loc["id"], "quantity": 1, "unit": "cup"},
    )
    assert resp.status_code == 400
    assert "convert" in resp.json()["detail"].lower()
    # Stock untouched.
    rows = client.get(f"/api/inventory?location_id={loc['id']}").json()["items"]
    assert Decimal(str(rows[0]["quantity"])) == Decimal("3")
