"""One item, every stage: restock (packages) → shopping list (packages) →
store handoff → purchase line (packages) → pantry (base ml) → recipe (cups)
→ cook (ml subtracted) → restock again. Each stage must agree on units."""

from __future__ import annotations

import httpx
import pytest

from app.core.config import settings


@pytest.fixture(autouse=True)
def _personal(monkeypatch):
    monkeypatch.setattr(settings, "auth_enabled", False)


def _on_hand(client: httpx.Client, loc: str, item_id: str) -> float:
    rows = client.get(f"/api/inventory?location_id={loc}").json()["items"]
    return sum(float(r["quantity"]) for r in rows if r["item_id"] == item_id)


def _entry(client: httpx.Client, item_id: str):
    return next((e for e in client.get("/api/shopping-list").json()["items"] if e["item_id"] == item_id and e["status"] == "pending"), None)


def test_milk_maps_correctly_through_every_stage(client: httpx.Client) -> None:
    loc = client.post("/api/locations", json={"name": "Fridge"}).json()["id"]
    milk = client.post(
        "/api/items",
        json={
            "name": "Whole milk",
            "stock_mode": "measured",
            "size_quantity": 64,
            "size_unit": "floz",  # one jug ≈ 1892.7 ml
            "restock_threshold": 2,
            "restock_unit": "package",  # "keep 2 jugs"
        },
    ).json()
    assert milk["base_unit"] in (None, "ml")

    # 1. Nothing on hand → buy 2 packages.
    entry = _entry(client, milk["id"])
    assert (float(entry["quantity"]), entry["unit"]) == (2.0, "package")

    # 2. Store handoff → purchase line in packages, pantry in ml.
    session = client.post("/api/shopping-list/handoff", json={"retailer": "walmart"}).json()
    row = next(i for i in session["items"] if i["item_id"] == milk["id"])
    assert row["unit"] == "package"
    client.patch(f"/api/shopping-list/handoff/{session['id']}/items/{row['id']}", json={"state": "added"})
    done = client.post(
        f"/api/shopping-list/handoff/{session['id']}/complete",
        json={"purchased_item_ids": [row["id"]], "location_id": loc},
    ).json()
    lines = client.get(f"/api/purchases/{done['purchase_id']}").json()["lines"]
    assert (float(lines[0]["parsed_quantity"]), lines[0]["parsed_unit"]) == (2.0, "package")
    assert _on_hand(client, loc, milk["id"]) == pytest.approx(3785.4, abs=1)
    assert _entry(client, milk["id"]) is None  # 2 jugs = at threshold

    # 3. Recipe in cups → cooking subtracts ml.
    recipe = client.post("/api/recipes", json={"name": "Pancakes"}).json()
    put = client.put(
        f"/api/recipes/{recipe['id']}/ingredients",
        json=[{"item_id": milk["id"], "quantity_required": 2, "unit": "cup"}],
    )
    assert put.status_code == 200, put.text
    cook = client.post(f"/api/recipes/{recipe['id']}/cook", json={"location_id": loc, "servings": 1, "leftover_servings": 0})
    assert cook.status_code == 200, cook.text
    assert _on_hand(client, loc, milk["id"]) == pytest.approx(3785.4 - 473.2, abs=1)

    # 4. Now under 2 jugs → buy 1 package again.
    entry = _entry(client, milk["id"])
    assert (float(entry["quantity"]), entry["unit"]) == (1.0, "package")
