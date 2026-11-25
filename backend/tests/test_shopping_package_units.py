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
