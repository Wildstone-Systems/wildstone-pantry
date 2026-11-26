"""Reorder entries and cart quantities must agree on what a "package" is: the
shopping list orders whole packages, while on-hand is tracked in base units."""

from __future__ import annotations

import httpx
import pytest

from app.core.config import settings
from app.db import SessionLocal
from app.models import Item
from modules.shopping.services.shopping_list import _cart_quantity


@pytest.fixture(autouse=True)
def _personal(monkeypatch):
    monkeypatch.setattr(settings, "auth_enabled", False)


@pytest.mark.parametrize(
    ("name", "body", "packages"),
    [
        ("Multipack", {"stock_mode": "counted", "size_quantity": 20, "size_unit": "ea", "restock_threshold": 30}, 2),
        ("Chips", {"stock_mode": "counted", "size_quantity": 16, "size_unit": "oz", "restock_threshold": 3}, 3),
        ("Milk", {"stock_mode": "measured", "size_quantity": 64, "size_unit": "floz", "restock_threshold": 3000}, 2),
        ("Flour", {"stock_mode": "measured", "size_quantity": 16, "size_unit": "oz", "restock_threshold": 1000}, 3),
        # Base ml but package sized by weight, no density → no bridge. Must not
        # order 1000 packages for a 1000 ml shortfall.
        (
            "Syrup",
            {"stock_mode": "measured", "size_quantity": 16, "size_unit": "oz", "base_unit": "ml", "restock_threshold": 1000},
            1,
        ),
        ("Lemons", {"stock_mode": "counted", "restock_threshold": 4}, 4),
    ],
)
def test_reorder_orders_whole_packages(client: httpx.Client, name, body, packages) -> None:
    item = client.post("/api/items", json={"name": name, **body}).json()
    entry = next(e for e in client.get("/api/shopping-list").json()["items"] if e["item_id"] == item["id"])
    with SessionLocal() as db:
        assert _cart_quantity(entry, db.get(Item, item["id"])) == packages


def _on_hand(client: httpx.Client, item_id: str, location_id: str) -> float:
    rows = client.get(f"/api/inventory?location_id={location_id}").json()["items"]
    return sum(float(r["quantity"]) for r in rows if r["item_id"] == item_id)


def test_reorder_entries_are_counted_in_packages(client: httpx.Client) -> None:
    item = client.post(
        "/api/items",
        json={"name": "Pouches", "stock_mode": "counted", "size_quantity": 20, "size_unit": "ea", "restock_threshold": 30},
    ).json()
    entry = next(e for e in client.get("/api/shopping-list").json()["items"] if e["item_id"] == item["id"])
    assert (float(entry["quantity"]), entry["unit"]) == (2.0, "package")


def test_manual_entries_default_to_package(client: httpx.Client) -> None:
    entry = client.post("/api/shopping-list", json={"name": "Paper towels", "quantity": 2}).json()
    assert entry["unit"] == "package"


def test_package_unit_means_whole_packages_in_and_out(client: httpx.Client) -> None:
    loc = client.post("/api/locations", json={"name": "Pantry"}).json()
    item = client.post(
        "/api/items", json={"name": "Snack box", "stock_mode": "counted", "size_quantity": 20, "size_unit": "ea"}
    ).json()
    ref = {"item_id": item["id"], "location_id": loc["id"]}

    # Buying 2 packages stocks 40 pouches; 'each' still means one pouch.
    assert client.post("/api/inventory/add", json={**ref, "quantity": 2, "unit": "package"}).status_code < 300
    assert _on_hand(client, item["id"], loc["id"]) == 40
    assert client.post("/api/inventory/remove", json={**ref, "quantity": 1, "unit": "each"}).status_code < 300
    assert _on_hand(client, item["id"], loc["id"]) == 39
    assert client.post("/api/inventory/remove", json={**ref, "quantity": 1, "unit": "package"}).status_code < 300
    assert _on_hand(client, item["id"], loc["id"]) == 19


def _entry(client: httpx.Client, item_id: str):
    return next((e for e in client.get("/api/shopping-list").json()["items"] if e["item_id"] == item_id), None)


def test_restock_threshold_can_be_counted_in_packages(client: httpx.Client) -> None:
    loc = client.post("/api/locations", json={"name": "Fridge"}).json()
    milk = client.post(
        "/api/items",
        json={
            "name": "Milk jug",
            "stock_mode": "measured",
            "size_quantity": 64,
            "size_unit": "floz",
            "restock_threshold": 2,
            "restock_unit": "package",
        },
    ).json()
    assert milk["restock_unit"] == "package"
    ref = {"item_id": milk["id"], "location_id": loc["id"]}

    # 1 jug on hand < 2 jugs → buy 1 package (not "2 ml short").
    client.post("/api/inventory/add", json={**ref, "quantity": 1, "unit": "package"})
    entry = _entry(client, milk["id"])
    assert (float(entry["quantity"]), entry["unit"]) == (1.0, "package")

    # 2 jugs on hand → at threshold, off the list.
    client.post("/api/inventory/add", json={**ref, "quantity": 1, "unit": "package"})
    assert _entry(client, milk["id"]) is None


def test_restock_unit_defaults_to_base(client: httpx.Client) -> None:
    item = client.post("/api/items", json={"name": "Rice", "restock_threshold": 3}).json()
    assert item["restock_unit"] == "base"


def test_package_threshold_without_size_is_ignored_for_measured(client: httpx.Client) -> None:
    item = client.post(
        "/api/items",
        json={
            "name": "Mystery syrup",
            "stock_mode": "measured",
            "size_quantity": 16,
            "size_unit": "oz",
            "base_unit": "ml",  # ml stock, weight-sized package, no density
            "restock_threshold": 1,
            "restock_unit": "package",
        },
    ).json()
    assert _entry(client, item["id"]) is None  # no bridge → never guess


def test_one_stocked_package_meets_a_one_package_threshold(client: httpx.Client) -> None:
    """16 oz = 453.59237 g, but on-hand stores 453.592 g. A full shelf must not reorder."""
    loc = client.post("/api/locations", json={"name": "Shelf"}).json()
    item = client.post(
        "/api/items",
        json={
            "name": "Flour bag",
            "stock_mode": "measured",
            "size_quantity": 16,
            "size_unit": "oz",
            "restock_threshold": 1,
            "restock_unit": "package",
        },
    ).json()
    client.post(
        "/api/inventory/add",
        json={"item_id": item["id"], "location_id": loc["id"], "quantity": 1, "unit": "package"},
    )
    assert _entry(client, item["id"]) is None
