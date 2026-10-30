"""Counted multipacks (ADR 0014 follow-up): a 20-pouch variety pack is bought by
the pack but eaten per pouch. Intake explodes a pack into its units so "use 1"
takes one pouch; the shopping list still orders whole packs so a small shortfall
can't balloon into 20 packs at the Kroger cart."""

from __future__ import annotations

import httpx
import pytest

from app.core.config import settings


@pytest.fixture(autouse=True)
def _personal(monkeypatch) -> None:
    monkeypatch.setattr(settings, "auth_enabled", False)


def _pack_item(client: httpx.Client, name="Variety Pack", size=20, threshold=None) -> str:
    body = {"name": name, "size_quantity": size, "size_unit": "ea"}
    if threshold is not None:
        body["restock_threshold"] = threshold
    return client.post("/api/items", json=body).json()["id"]


def _loc(client: httpx.Client, name="Pantry") -> str:
    return client.post("/api/locations", json={"name": name}).json()["id"]


def _on_hand(client: httpx.Client, loc: str, item: str) -> float:
    rows = client.get(f"/api/inventory?location_id={loc}").json()["items"]
    return float(next((r["quantity"] for r in rows if r["item_id"] == item), 0))


def test_adding_a_pack_stocks_its_units_and_use_one_takes_one(client: httpx.Client) -> None:
    item = _pack_item(client)
    loc = _loc(client)
    # Buy one 20-pack.
    client.post("/api/inventory/add", json={"item_id": item, "location_id": loc, "quantity": 1, "unit": "each"})
    assert _on_hand(client, loc, item) == 20.0  # exploded to pouches

    # Eat one pouch.
    client.post("/api/inventory/remove", json={"item_id": item, "location_id": loc, "quantity": 1, "unit": "each"})
    assert _on_hand(client, loc, item) == 19.0


def test_single_unit_item_is_not_exploded(client: httpx.Client) -> None:
    item = _pack_item(client, name="Single Can", size=1)
    loc = _loc(client)
    client.post("/api/inventory/add", json={"item_id": item, "location_id": loc, "quantity": 3, "unit": "each"})
    assert _on_hand(client, loc, item) == 3.0  # size 1 ea → no explode


def test_restock_orders_packs_not_pouches(client: httpx.Client) -> None:
    item = _pack_item(client, threshold=5)  # restock when below 5 pouches
    loc = _loc(client)
    client.post("/api/inventory/add", json={"item_id": item, "location_id": loc, "quantity": 1, "unit": "each"})
    # Eat down to 4 pouches — one below the threshold.
    client.post("/api/inventory/remove", json={"item_id": item, "location_id": loc, "quantity": 16, "unit": "each"})
    assert _on_hand(client, loc, item) == 4.0

    entries = client.get("/api/shopping-list").json()["items"]
    auto = next(e for e in entries if e["item_id"] == item and e["source"] == "auto")
    # 1 pouch short → ceil(1/20) = ONE pack, not 1 pouch and never 20.
    assert float(auto["quantity"]) == 1.0


def test_measured_item_stores_grams_and_restocks_by_the_jar(client: httpx.Client) -> None:
    # A measured jar (2 oz cinnamon) has base g; buying 1 jar stores ~56.7 g, and a
    # gram-level shortfall reorders whole JARS, never grams.
    item = client.post(
        "/api/items",
        json={"name": "Ground Cinnamon", "stock_mode": "measured", "size_quantity": 2, "size_unit": "oz",
              "restock_threshold": 10},
    ).json()["id"]
    loc = _loc(client, "Spice Rack")
    client.post("/api/inventory/add", json={"item_id": item, "location_id": loc, "quantity": 1, "unit": "each"})
    assert _on_hand(client, loc, item) == pytest.approx(56.7, abs=0.5)  # 2 oz → g

    # Use 50 g (drops below the 10 g threshold).
    client.post("/api/inventory/remove", json={"item_id": item, "location_id": loc, "quantity": 50, "unit": "g"})
    entries = client.get("/api/shopping-list").json()["items"]
    auto = next(e for e in entries if e["item_id"] == item and e["source"] == "auto")
    # ~3.3 g short → ceil(3.3 / 56.7) = ONE jar, and the entry is a package (each).
    assert float(auto["quantity"]) == 1.0
    assert auto["unit"] == "each"


def test_flipping_tracking_recomputes_base_unit(client: httpx.Client) -> None:
    # A weight-labelled snack tracked as bags (counted) has base 'each'; flipping it
    # to measured re-derives the base to its size dimension (g) — ADR 0016 Phase 3.
    item = client.post(
        "/api/items",
        json={"name": "Chips", "stock_mode": "counted", "size_quantity": 150, "size_unit": "g"},
    ).json()
    assert item["base_unit"] == "each"

    upd = client.put(f"/api/items/{item['id']}", json={"stock_mode": "measured"})
    assert upd.status_code == 200, upd.text
    assert upd.json()["base_unit"] == "g"


def test_container_batch_stores_base_units_and_tracks_the_jugs(client: httpx.Client) -> None:
    # ADR 0014 × 0016: checking in 2 sealed 0.5-gal jugs stores the balance in the
    # base unit (ml) — 2 × 1892.7 — while the batch still knows it's 2 containers.
    item = client.post(
        "/api/items",
        json={"name": "Whole Milk", "stock_mode": "measured", "size_quantity": 1, "size_unit": "gal"},
    ).json()["id"]
    loc = _loc(client, "Fridge")
    resp = client.post(
        "/api/inventory/add",
        json={
            "item_id": item,
            "location_id": loc,
            "quantity": 1,  # ignored when the container form is present
            "sealed_count": 2,
            "container_size": 0.5,
            "container_unit": "gal",
            "expiration_date": "2026-08-09",
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    # Balance is in base ml, and the response reports the base delta for the toast.
    assert _on_hand(client, loc, item) == pytest.approx(3785.4, abs=1.0)
    assert float(body["added_quantity"]) == pytest.approx(3785.4, abs=1.0)
    assert body["added_unit"] == "ml"

    # The item's package size is refreshed from what was checked in (½ gal), so the
    # next scan prefills it.
    got = client.get(f"/api/items/{item}").json()
    assert float(got["size_quantity"]) == 0.5
    assert got["size_unit"] == "gal"

    # Consuming one jug's worth (1 each) draws a whole container: 3785.4 - 1892.7.
    client.post("/api/inventory/remove", json={"item_id": item, "location_id": loc, "quantity": 1, "unit": "each"})
    assert _on_hand(client, loc, item) == pytest.approx(1892.7, abs=1.0)


def test_container_batch_of_counted_cartons_stores_eaches(client: httpx.Client) -> None:
    # A counted item (eggs) bought as 2 cartons of 12 stores 24 each — base 'each' —
    # and the container size rides along in the same unit.
    item = client.post(
        "/api/items",
        json={"name": "Eggs", "stock_mode": "counted", "size_quantity": 12, "size_unit": "ea"},
    ).json()["id"]
    loc = _loc(client, "Egg Shelf")
    resp = client.post(
        "/api/inventory/add",
        json={"item_id": item, "location_id": loc, "quantity": 1, "sealed_count": 2,
              "container_size": 12, "container_unit": "each"},
    )
    assert resp.status_code == 200, resp.text
    assert _on_hand(client, loc, item) == 24.0
    assert resp.json()["added_unit"] == "each"


def _seed_ref() -> None:
    from app.db.session import SessionLocal
    from modules.catalog.services.ingredient_reference import seed_reference

    db = SessionLocal()
    try:
        seed_reference(db)
    finally:
        db.close()


def test_reference_density_lets_you_consume_a_spice_by_the_teaspoon(client: httpx.Client) -> None:
    # A measured spice with NO per-item density can still be used by tsp: the seeded
    # ingredient_reference supplies the density (ADR 0016 Phase 2).
    _seed_ref()
    item = client.post(
        "/api/items",
        json={"name": "Cumin", "stock_mode": "measured", "size_quantity": 2, "size_unit": "oz"},
    ).json()["id"]
    loc = _loc(client, "Rack")
    client.post("/api/inventory/add", json={"item_id": item, "location_id": loc, "quantity": 1, "unit": "each"})
    assert _on_hand(client, loc, item) == pytest.approx(56.7, abs=0.5)  # 2 oz → g

    resp = client.post("/api/inventory/remove", json={"item_id": item, "location_id": loc, "quantity": 1, "unit": "tsp"})
    assert resp.status_code == 200, resp.text
    # 1 tsp = 4.929 ml × 0.52 g/ml (seed) = 2.56 g → 56.7 - 2.56 = 54.1 g.
    assert _on_hand(client, loc, item) == pytest.approx(54.1, abs=0.5)


def test_reference_density_consume_milk_by_mass(client: httpx.Client) -> None:
    _seed_ref()
    item = client.post(
        "/api/items", json={"name": "milk", "stock_mode": "measured", "size_quantity": 1, "size_unit": "gal"}
    ).json()["id"]
    loc = _loc(client, "Fridge2")
    client.post("/api/inventory/add", json={"item_id": item, "location_id": loc, "quantity": 1, "unit": "each"})
    # Use 100 g of milk — g→ml needs density; seed 'milk' = 1.03.
    resp = client.post("/api/inventory/remove", json={"item_id": item, "location_id": loc, "quantity": 100, "unit": "g"})
    assert resp.status_code == 200, resp.text
    # 3785.41 - 100/1.03 (97.1 ml) = 3688.3 ml.
    assert _on_hand(client, loc, item) == pytest.approx(3688.3, abs=1.0)
