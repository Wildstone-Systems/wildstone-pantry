"""Container intake / open / discard through the inventory API (ADR 0014 step 4).

End-to-end over the real endpoints: intake records a batch of containers, a
partial consume auto-opens one, and discarding a specific container leaves the
others (and their expiry) untouched.
"""

from __future__ import annotations

import httpx
import pytest

from app.core.config import settings


@pytest.fixture(autouse=True)
def _personal(monkeypatch):
    monkeypatch.setattr(settings, "auth_enabled", False)


def _loc(client: httpx.Client, name: str) -> dict:
    return client.post("/api/locations", json={"name": name}).json()


def _item(client: httpx.Client, name: str, unit: str) -> dict:
    return client.post("/api/items", json={"name": name, "size_quantity": 1, "size_unit": unit}).json()


def _lots(client: httpx.Client, item_id: str, loc_id: str) -> list[dict]:
    return client.get("/api/inventory/lots", params={"item_id": item_id, "location_id": loc_id}).json()["lots"]


def _balance(client: httpx.Client, item_id: str, loc_id: str) -> float:
    rows = client.get("/api/inventory", params={"location_id": loc_id}).json()["items"]
    row = next((r for r in rows if r["item_id"] == item_id), None)
    return float(row["quantity"]) if row else 0.0


def test_milk_container_lifecycle(client: httpx.Client) -> None:
    loc, item = _loc(client, "Fridge"), _item(client, "Milk", "gal")

    # Intake: two sealed gallons (one batch, two containers).
    r = client.post(
        "/api/inventory/add",
        json={
            "item_id": item["id"],
            "location_id": loc["id"],
            "quantity": 1,  # ignored — container fields drive quantity/unit
            "unit": "gal",
            "sealed_count": 2,
            "container_size": 1,
            "container_unit": "gal",
        },
    )
    assert r.status_code == 200, r.text
    # Stored in base units (ADR 0016/0017): 2 sealed gallons = 2 × 3785.41 ml.
    lots = _lots(client, item["id"], loc["id"])
    assert len(lots) == 1 and lots[0]["status"] == "sealed" and lots[0]["sealed_count"] == 2
    assert _balance(client, item["id"], loc["id"]) == pytest.approx(7570.82, abs=0.1)

    # Use half a gallon (1892.7 ml) → auto-opens one container.
    client.post(
        "/api/inventory/remove",
        json={"item_id": item["id"], "location_id": loc["id"], "quantity": 0.5, "unit": "gal"},
    )
    lots = _lots(client, item["id"], loc["id"])
    sealed = next(l for l in lots if l["status"] == "sealed")
    open_ctr = next(l for l in lots if l["status"] == "open")
    assert sealed["sealed_count"] == 1 and float(sealed["quantity_remaining"]) == pytest.approx(3785.41, abs=0.1)
    assert float(open_ctr["quantity_remaining"]) == pytest.approx(1892.705, abs=0.1)

    # The open jug spoils — discard that specific container.
    r = client.post("/api/inventory/discard", json={"lot_id": open_ctr["lot_id"]})
    assert r.status_code == 200, r.text
    lots = r.json()["lots"]
    assert all(l["status"] != "open" for l in lots)
    sealed = next(l for l in lots if float(l["quantity_remaining"]) > 0)
    assert sealed["sealed_count"] == 1 and float(sealed["quantity_remaining"]) == pytest.approx(3785.41, abs=0.1)
    assert _balance(client, item["id"], loc["id"]) == pytest.approx(3785.41, abs=0.1)  # untouched sealed gallon


def test_yogurt_discard_one_sealed_tub_of_a_batch(client: httpx.Client) -> None:
    loc, item = _loc(client, "Fridge2"), _item(client, "Yogurt", "qt")

    def add(count: int, exp: str) -> None:
        client.post(
            "/api/inventory/add",
            json={
                "item_id": item["id"],
                "location_id": loc["id"],
                "quantity": 1,
                "unit": "qt",
                "sealed_count": count,
                "container_size": 1,
                "container_unit": "qt",
                "expiration_date": exp,
            },
        )

    add(1, "2026-08-08")
    add(1, "2026-08-11")
    add(4, "2026-08-15")

    big = next(l for l in _lots(client, item["id"], loc["id"]) if l["sealed_count"] == 4)
    r = client.post("/api/inventory/discard", json={"lot_id": big["lot_id"]})  # one sealed tub
    assert r.status_code == 200, r.text

    by_id = {l["lot_id"]: l for l in r.json()["lots"]}
    assert by_id[big["lot_id"]]["sealed_count"] == 3  # only the four-tub lot dropped by one
    others = [l for l in r.json()["lots"] if l["lot_id"] != big["lot_id"]]
    assert all(l["sealed_count"] == 1 for l in others)  # the two single-tub lots untouched
    # 6 − 1 discarded = 5 quarts, stored in base ml (5 × 946.353).
    assert _balance(client, item["id"], loc["id"]) == pytest.approx(4731.77, abs=0.1)


def test_set_item_expiration_updates_the_on_hand_best_by(client: httpx.Client) -> None:
    loc, item = _loc(client, "Fridge3"), _item(client, "Milk2", "gal")
    client.post(
        "/api/inventory/add",
        json={"item_id": item["id"], "location_id": loc["id"], "quantity": 1, "unit": "gal"},
    )
    # No date yet.
    rows = client.get("/api/inventory", params={"location_id": loc["id"]}).json()["items"]
    assert next(r for r in rows if r["item_id"] == item["id"])["expiration_date"] is None

    r = client.post(
        "/api/inventory/set-expiration",
        json={"item_id": item["id"], "location_id": loc["id"], "expiration_date": "2026-08-01"},
    )
    assert r.status_code == 200 and r.json()["relabeled"] >= 1

    rows = client.get("/api/inventory", params={"location_id": loc["id"]}).json()["items"]
    assert next(r for r in rows if r["item_id"] == item["id"])["expiration_date"] == "2026-08-01"


def test_per_lot_relabel_only_touches_that_lot(client: httpx.Client) -> None:
    loc, item = _loc(client, "Fridge4"), _item(client, "Milk3", "gal")
    # Two separate gallons: one dated, one not (the reported scenario).
    client.post(
        "/api/inventory/add",
        json={"item_id": item["id"], "location_id": loc["id"], "quantity": 1, "unit": "gal", "expiration_date": "2026-08-01"},
    )
    client.post("/api/inventory/add", json={"item_id": item["id"], "location_id": loc["id"], "quantity": 1, "unit": "gal"})
    lots = _lots(client, item["id"], loc["id"])
    assert len(lots) == 2
    undated = next(l for l in lots if l["expiration_date"] is None)
    dated = next(l for l in lots if l["expiration_date"] == "2026-08-01")

    r = client.post(f"/api/inventory/lots/{undated['lot_id']}/relabel", json={"expiration_date": "2026-08-05"})
    assert r.status_code == 200, r.text

    by_id = {l["lot_id"]: l for l in _lots(client, item["id"], loc["id"])}
    assert by_id[undated["lot_id"]]["expiration_date"] == "2026-08-05"  # the one we set
    assert by_id[dated["lot_id"]]["expiration_date"] == "2026-08-01"  # untouched


def test_lot_freeze_thaw_and_spoil_paths(client: httpx.Client) -> None:
    loc, item = _loc(client, "Freezer"), _item(client, "Peas", "each")
    client.post("/api/inventory/add", json={"item_id": item["id"], "location_id": loc["id"], "quantity": 2, "unit": "each"})
    lot = _lots(client, item["id"], loc["id"])[0]

    assert client.post(f"/api/inventory/lots/{lot['lot_id']}/freeze").status_code == 200
    assert next(l for l in _lots(client, item["id"], loc["id"]) if l["lot_id"] == lot["lot_id"])["frozen"] is True
    assert client.post(f"/api/inventory/lots/{lot['lot_id']}/thaw").status_code == 200
    assert next(l for l in _lots(client, item["id"], loc["id"]) if l["lot_id"] == lot["lot_id"])["frozen"] is False

    assert client.post(f"/api/inventory/lots/{lot['lot_id']}/spoil").status_code == 200
    assert _balance(client, item["id"], loc["id"]) == 0.0


def test_remove_lot_correction_is_not_consumption_or_waste(client: httpx.Client) -> None:
    loc, item = _loc(client, "Pantry"), _item(client, "MilkX", "gal")
    client.post("/api/inventory/add", json={"item_id": item["id"], "location_id": loc["id"], "quantity": 1, "unit": "gal"})
    lot = _lots(client, item["id"], loc["id"])[0]

    r = client.post(f"/api/inventory/lots/{lot['lot_id']}/remove")
    assert r.status_code == 200, r.text
    assert _balance(client, item["id"], loc["id"]) == 0.0  # gone

    events = [e for e in client.get("/api/inventory/events").json()["events"] if e["item_id"] == item["id"]]
    types = {e["event_type"] for e in events}
    assert "consumed" not in types  # not a false "used" signal
    assert "spoiled" not in types  # not a false "wasted" signal
    assert any(e["event_type"] == "adjusted" and e["reason"] == "correction" for e in events)
