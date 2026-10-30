"""Scanning an empty container out — ADR 0023 D2/D3/D6.

The yogurt case end to end. A household keeps several quart tubs going; someone
eats part of one, then throws the empty tub in the bin and scans it.

Before this, that scan converted "1 each" through the item's package size into a
FULL container's worth of content, so it drained the open tub, cracked a sealed
one to cover the difference, and errored outright on the last tub. These tests
pin the corrected behaviour: a scan-out of a container-tracked item finishes a
container, and when more than one is open it asks instead of guessing.
"""

from __future__ import annotations

import httpx


def _headers(client: httpx.Client) -> dict[str, str]:
    client.post("/api/auth/bootstrap-admin", json={"email": "admin@pantryos.local", "password": "TestPass123!"})
    login = client.post("/api/auth/login", json={"email": "admin@pantryos.local", "password": "TestPass123!"})
    return {"Authorization": f"Bearer {login.json()['token']}"}


def _item(client, h, name, barcode) -> str:
    return client.post("/api/items", json={"name": name, "barcode": barcode}, headers=h).json()["id"]


def _loc(client, h, name) -> str:
    return client.post("/api/locations", json={"name": name}, headers=h).json()["id"]


def _stock_tubs(client, h, item_id, loc, count=3, size=1, unit="qt", expiry=None):
    """Check in ``count`` sealed containers of ``size`` (ADR 0014 batch intake)."""
    body = {
        "item_id": item_id,
        "location_id": loc,
        "quantity": count,
        "sealed_count": count,
        "container_size": size,
        "container_unit": unit,
    }
    if expiry:
        body["expiration_date"] = expiry
    resp = client.post("/api/inventory/add", json=body, headers=h)
    assert resp.status_code == 200, resp.text
    return resp.json()


def _lots(client, h, item_id, loc):
    resp = client.get(f"/api/inventory/lots?item_id={item_id}&location_id={loc}", headers=h)
    assert resp.status_code == 200, resp.text
    return resp.json()["lots"]


def _on_hand(client, h, item_id, loc) -> float:
    rows = client.get(f"/api/inventory?location_id={loc}", headers=h).json()["items"]
    return next((float(r["quantity"]) for r in rows if r["item_id"] == item_id), 0.0)


def _scan_out(client, h, barcode, **extra):
    return client.post(
        "/api/inventory/scan", json={"barcode": barcode, "direction": "out", "quantity": 1, **extra}, headers=h
    )


def test_scan_out_finishes_the_open_tub_without_opening_another(client: httpx.Client) -> None:
    """Half a tub left, empty tub scanned out: the balance must drop by the
    remainder, not by a whole container."""
    h = _headers(client)
    item = _item(client, h, "Yogurt", "0200000000001")
    fridge = _loc(client, h, "Fridge")
    _stock_tubs(client, h, item, fridge, count=3, size=1)

    full = _on_hand(client, h, item, fridge)

    # Eat half a tub — auto-opens one container (ADR 0014).
    client.post(
        "/api/inventory/remove",
        json={"item_id": item, "location_id": fridge, "quantity": full / 6, "unit": "qt"},
        headers=h,
    )
    opened = [lot for lot in _lots(client, h, item, fridge) if lot["status"] == "open"]
    assert len(opened) == 1

    before = _on_hand(client, h, item, fridge)
    remainder = float(opened[0]["quantity_remaining"])

    resp = _scan_out(client, h, "0200000000001")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "done"

    after = _on_hand(client, h, item, fridge)
    assert round(before - after, 3) == round(remainder, 3)  # only what was left
    assert [lot for lot in _lots(client, h, item, fridge) if lot["status"] == "open"] == []  # no phantom open tub


def test_scan_out_asks_which_container_when_several_are_open(client: httpx.Client) -> None:
    """Two open tubs: which one was emptied is the human's fact, so the scan
    must prompt and change nothing (ADR 0023 D3)."""
    h = _headers(client)
    item = _item(client, h, "Yogurt", "0200000000002")
    fridge = _loc(client, h, "Fridge")
    _stock_tubs(client, h, item, fridge, count=3, size=1)

    lots = _lots(client, h, item, fridge)
    batch_id = lots[0]["lot_id"]
    # Open two containers explicitly, so two partial tubs are live at once.
    client.post(f"/api/inventory/lots/{batch_id}/open", headers=h)
    client.post(f"/api/inventory/lots/{batch_id}/open", headers=h)
    open_lots = [lot for lot in _lots(client, h, item, fridge) if lot["status"] == "open"]
    assert len(open_lots) == 2

    before = _on_hand(client, h, item, fridge)
    resp = _scan_out(client, h, "0200000000002")
    assert resp.status_code == 200, resp.text
    body = resp.json()

    assert body["status"] == "needs_container"
    assert len(body["candidate_containers"]) == 2
    assert body["transaction_id"] is None
    assert _on_hand(client, h, item, fridge) == before  # nothing moved


def test_answering_the_prompt_consumes_that_container(client: httpx.Client) -> None:
    h = _headers(client)
    item = _item(client, h, "Yogurt", "0200000000003")
    fridge = _loc(client, h, "Fridge")
    _stock_tubs(client, h, item, fridge, count=3, size=1)
    batch_id = _lots(client, h, item, fridge)[0]["lot_id"]
    client.post(f"/api/inventory/lots/{batch_id}/open", headers=h)
    client.post(f"/api/inventory/lots/{batch_id}/open", headers=h)

    chosen = _scan_out(client, h, "0200000000003").json()["candidate_containers"][0]
    before = _on_hand(client, h, item, fridge)

    resp = _scan_out(client, h, "0200000000003", lot_id=chosen["lot_id"])
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "done"
    assert body["transaction_id"] is not None

    after = _on_hand(client, h, item, fridge)
    assert round(before - after, 3) == round(float(chosen["quantity_remaining"]), 3)
    # The other open tub is untouched.
    assert len([lot for lot in _lots(client, h, item, fridge) if lot["status"] == "open"]) == 1


def test_scanning_out_the_last_tub_succeeds(client: httpx.Client) -> None:
    """The old conversion path raised "Insufficient inventory quantity" on the
    final container — an error thrown at the person doing the right thing."""
    h = _headers(client)
    item = _item(client, h, "Yogurt", "0200000000004")
    fridge = _loc(client, h, "Fridge")
    _stock_tubs(client, h, item, fridge, count=1, size=1)

    full = _on_hand(client, h, item, fridge)
    client.post(
        "/api/inventory/remove",
        json={"item_id": item, "location_id": fridge, "quantity": full / 2, "unit": "qt"},
        headers=h,
    )

    resp = _scan_out(client, h, "0200000000004")
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "done"
    assert _on_hand(client, h, item, fridge) == 0.0


def test_scan_out_is_undoable_and_restores_the_same_container(client: httpx.Client) -> None:
    """ADR 0023 D6: the scan hands back a transaction id, and reversing it puts
    the content back in the tub it came out of — same lot, same expiry."""
    h = _headers(client)
    item = _item(client, h, "Yogurt", "0200000000005")
    fridge = _loc(client, h, "Fridge")
    _stock_tubs(client, h, item, fridge, count=2, size=1, expiry="2026-12-01")

    full = _on_hand(client, h, item, fridge)
    client.post(
        "/api/inventory/remove",
        json={"item_id": item, "location_id": fridge, "quantity": full / 4, "unit": "qt"},
        headers=h,
    )
    open_lot = next(lot for lot in _lots(client, h, item, fridge) if lot["status"] == "open")
    before = _on_hand(client, h, item, fridge)

    tx_id = _scan_out(client, h, "0200000000005").json()["transaction_id"]
    assert tx_id is not None
    assert _on_hand(client, h, item, fridge) < before

    resp = client.post(f"/api/inventory/transactions/{tx_id}/reverse", headers=h)
    assert resp.status_code == 200, resp.text

    assert _on_hand(client, h, item, fridge) == before
    revived = next(lot for lot in _lots(client, h, item, fridge) if lot["lot_id"] == open_lot["lot_id"])
    assert float(revived["quantity_remaining"]) == float(open_lot["quantity_remaining"])
    assert revived["expiration_date"] == open_lot["expiration_date"]  # identity survived the undo


def test_finishing_an_already_empty_container_is_a_no_op(client: httpx.Client) -> None:
    """Two people reach for the same empty tub (ADR 0023 D13). The second tap
    must not fall through and consume something else."""
    h = _headers(client)
    item = _item(client, h, "Yogurt", "0200000000006")
    fridge = _loc(client, h, "Fridge")
    _stock_tubs(client, h, item, fridge, count=2, size=1)
    batch_id = _lots(client, h, item, fridge)[0]["lot_id"]
    client.post(f"/api/inventory/lots/{batch_id}/open", headers=h)
    open_lot = next(lot for lot in _lots(client, h, item, fridge) if lot["status"] == "open")

    first = client.post(f"/api/inventory/lots/{open_lot['lot_id']}/finish", headers=h)
    assert first.status_code == 200, first.text
    assert first.json()["already_resolved"] is False
    after_first = _on_hand(client, h, item, fridge)

    second = client.post(f"/api/inventory/lots/{open_lot['lot_id']}/finish", headers=h)
    assert second.status_code == 200, second.text
    body = second.json()
    assert body["already_resolved"] is True
    assert body["transaction_id"] is None
    assert _on_hand(client, h, item, fridge) == after_first  # nothing else consumed


def test_partial_draw_from_a_named_container(client: httpx.Client) -> None:
    h = _headers(client)
    item = _item(client, h, "Yogurt", "0200000000007")
    fridge = _loc(client, h, "Fridge")
    _stock_tubs(client, h, item, fridge, count=2, size=1)
    batch_id = _lots(client, h, item, fridge)[0]["lot_id"]
    client.post(f"/api/inventory/lots/{batch_id}/open", headers=h)
    open_lot = next(lot for lot in _lots(client, h, item, fridge) if lot["status"] == "open")
    remaining = float(open_lot["quantity_remaining"])

    resp = client.post(
        f"/api/inventory/lots/{open_lot['lot_id']}/consume", json={"quantity": remaining / 2}, headers=h
    )
    assert resp.status_code == 200, resp.text

    still_open = next(lot for lot in _lots(client, h, item, fridge) if lot["lot_id"] == open_lot["lot_id"])
    assert round(float(still_open["quantity_remaining"]), 3) == round(remaining / 2, 3)


def test_throwing_out_records_waste_not_consumption(client: httpx.Client) -> None:
    """ADR 0023 D5: waste has to stay separable from usage in the ledger."""
    h = _headers(client)
    item = _item(client, h, "Yogurt", "0200000000008")
    fridge = _loc(client, h, "Fridge")
    _stock_tubs(client, h, item, fridge, count=2, size=1)
    batch_id = _lots(client, h, item, fridge)[0]["lot_id"]

    resp = client.post(f"/api/inventory/lots/{batch_id}/spoil", headers=h)
    assert resp.status_code == 200, resp.text
    assert resp.json()["transaction_id"] is not None  # spoils are undoable now

    events = client.get(f"/api/inventory/events?location_id={fridge}", headers=h).json()["events"]
    assert any(e["event_type"] == "spoiled" for e in events)


def test_non_container_items_keep_the_old_scan_out_behaviour(client: httpx.Client) -> None:
    """Plain quantity stock has no containers to finish — the aggregate path
    must be untouched."""
    h = _headers(client)
    item = _item(client, h, "Chips", "0200000000009")
    pantry = _loc(client, h, "Pantry")
    client.post(
        "/api/inventory/add",
        json={"item_id": item, "location_id": pantry, "quantity": 5, "unit": "each"},
        headers=h,
    )

    resp = _scan_out(client, h, "0200000000009")
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "done"
    assert _on_hand(client, h, item, pantry) == 4.0


def test_scan_in_creates_containers_so_the_loop_closes(client: httpx.Client) -> None:
    """ADR 0023 D9. The quick lane used to record a bare quantity, producing
    stock that could never be finished by container — intake and consumption
    have to be one design, not two."""
    h = _headers(client)
    item = _item(client, h, "Yogurt", "0200000000010")
    fridge = _loc(client, h, "Fridge")
    # A known package size on the catalog item is all the scan needs.
    client.put(f"/api/items/{item}", json={"size_quantity": 1, "size_unit": "qt"}, headers=h)

    resp = client.post(
        "/api/inventory/scan",
        json={"barcode": "0200000000010", "direction": "in", "quantity": 3},
        headers=h,
    )
    assert resp.status_code == 200, resp.text

    lots = _lots(client, h, item, fridge)
    assert any(lot["sealed_count"] == 3 for lot in lots), lots

    # And the consume side can now do its job on that stock.
    out = _scan_out(client, h, "0200000000010")
    assert out.status_code == 200, out.text
    assert out.json()["status"] == "done"
    assert any(lot["sealed_count"] == 2 for lot in _lots(client, h, item, fridge))


def test_scan_in_of_a_measured_amount_stays_container_less(client: httpx.Client) -> None:
    """"1.5 lb" is an amount, not a number of packages."""
    h = _headers(client)
    item = _item(client, h, "Mince", "0200000000011")
    fridge = _loc(client, h, "Fridge")
    client.put(f"/api/items/{item}", json={"size_quantity": 1, "size_unit": "lb"}, headers=h)

    resp = client.post(
        "/api/inventory/scan",
        json={"barcode": "0200000000011", "direction": "in", "quantity": 1.5, "unit": "lb"},
        headers=h,
    )
    assert resp.status_code == 200, resp.text
    assert all(lot["sealed_count"] in (None, 0) for lot in _lots(client, h, item, fridge))


def test_scan_out_rejects_a_container_belonging_to_another_item(client: httpx.Client) -> None:
    """A supplied lot_id is an ANSWER to this scan's prompt, so it has to belong
    to what was scanned. Otherwise a stale prompt or a crafted request could
    consume an unrelated item while the response reports the scanned one."""
    h = _headers(client)
    yogurt = _item(client, h, "Yogurt", "0200000000020")
    cream = _item(client, h, "Cream", "0200000000021")
    fridge = _loc(client, h, "Fridge")
    _stock_tubs(client, h, yogurt, fridge, count=2, size=1)
    _stock_tubs(client, h, cream, fridge, count=2, size=1)

    cream_lot = _lots(client, h, cream, fridge)[0]["lot_id"]
    cream_before = _on_hand(client, h, cream, fridge)

    resp = _scan_out(client, h, "0200000000020", lot_id=cream_lot)
    assert resp.status_code == 400, resp.text
    assert _on_hand(client, h, cream, fridge) == cream_before  # untouched


def test_scan_out_rejects_a_container_in_another_location(client: httpx.Client) -> None:
    h = _headers(client)
    yogurt = _item(client, h, "Yogurt", "0200000000022")
    fridge = _loc(client, h, "Fridge")
    garage = _loc(client, h, "Garage fridge")
    _stock_tubs(client, h, yogurt, fridge, count=2, size=1)
    _stock_tubs(client, h, yogurt, garage, count=2, size=1)

    garage_lot = _lots(client, h, yogurt, garage)[0]["lot_id"]
    garage_before = _on_hand(client, h, yogurt, garage)

    resp = client.post(
        "/api/inventory/scan",
        json={
            "barcode": "0200000000022",
            "direction": "out",
            "quantity": 1,
            "location_id": fridge,
            "lot_id": garage_lot,
        },
        headers=h,
    )
    assert resp.status_code == 400, resp.text
    assert _on_hand(client, h, yogurt, garage) == garage_before


def test_undoing_a_sealed_finish_restores_the_container_count(client: httpx.Client) -> None:
    """Reversal has to give back the container, not just the volume — a batch
    claiming fewer physical tubs than it holds misleads the worklist and the
    next scan's planning."""
    h = _headers(client)
    item = _item(client, h, "Yogurt", "0200000000023")
    fridge = _loc(client, h, "Fridge")
    _stock_tubs(client, h, item, fridge, count=3, size=1)

    batch = _lots(client, h, item, fridge)[0]
    assert batch["sealed_count"] == 3

    finish = client.post(f"/api/inventory/lots/{batch['lot_id']}/finish", headers=h)
    assert finish.status_code == 200, finish.text
    after = next(lot for lot in _lots(client, h, item, fridge) if lot["lot_id"] == batch["lot_id"])
    assert after["sealed_count"] == 2

    resp = client.post(f"/api/inventory/transactions/{finish.json()['transaction_id']}/reverse", headers=h)
    assert resp.status_code == 200, resp.text

    restored = next(lot for lot in _lots(client, h, item, fridge) if lot["lot_id"] == batch["lot_id"])
    assert restored["sealed_count"] == 3
    assert restored["status"] == "sealed"  # not revived as a phantom open tub
    assert float(restored["quantity_remaining"]) == float(batch["quantity_remaining"])


def test_undoing_the_last_sealed_container_restores_it_as_sealed(client: httpx.Client) -> None:
    """The single-container case is where a naive restore is worst: it revives
    the batch as `open`, so a full sealed tub reads as already started."""
    h = _headers(client)
    item = _item(client, h, "Yogurt", "0200000000024")
    fridge = _loc(client, h, "Fridge")
    _stock_tubs(client, h, item, fridge, count=1, size=1)
    batch = _lots(client, h, item, fridge)[0]

    finish = client.post(f"/api/inventory/lots/{batch['lot_id']}/finish", headers=h)
    client.post(f"/api/inventory/transactions/{finish.json()['transaction_id']}/reverse", headers=h)

    restored = _lots(client, h, item, fridge)[0]
    assert restored["sealed_count"] == 1
    assert restored["status"] == "sealed"


def test_undoing_an_open_container_finish_restores_it_as_open(client: httpx.Client) -> None:
    """The mirror case: a partially-used tub must come back open, not sealed."""
    h = _headers(client)
    item = _item(client, h, "Yogurt", "0200000000025")
    fridge = _loc(client, h, "Fridge")
    _stock_tubs(client, h, item, fridge, count=2, size=1)
    full = _on_hand(client, h, item, fridge)
    client.post(
        "/api/inventory/remove",
        json={"item_id": item, "location_id": fridge, "quantity": full / 4, "unit": "qt"},
        headers=h,
    )
    open_lot = next(lot for lot in _lots(client, h, item, fridge) if lot["status"] == "open")

    finish = client.post(f"/api/inventory/lots/{open_lot['lot_id']}/finish", headers=h)
    client.post(f"/api/inventory/transactions/{finish.json()['transaction_id']}/reverse", headers=h)

    restored = next(lot for lot in _lots(client, h, item, fridge) if lot["lot_id"] == open_lot["lot_id"])
    assert restored["status"] == "open"
    assert (restored["sealed_count"] or 0) == 0
