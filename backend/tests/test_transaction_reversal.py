"""Reversing a mistaken Log-tab entry (ADR 0002): inventory is an event-sourced
ledger, so undoing a mistake is a compensating transaction, not a hard delete —
both the original and the reversal stay visible in the log.
"""

from __future__ import annotations

from decimal import Decimal

import httpx


def _headers(client: httpx.Client) -> dict[str, str]:
    client.post("/api/auth/bootstrap-admin", json={"email": "reversal@pantryos.local", "password": "TestPass123!"})
    login = client.post("/api/auth/login", json={"email": "reversal@pantryos.local", "password": "TestPass123!"})
    return {"Authorization": f"Bearer {login.json()['token']}"}


def _seed(client: httpx.Client, h: dict[str, str]) -> tuple[str, str]:
    item = client.post("/api/items", json={"name": "Canned Beans"}, headers=h)
    assert item.status_code == 201, item.text
    loc = client.post("/api/locations", json={"name": "Pantry"}, headers=h)
    assert loc.status_code == 201, loc.text
    return item.json()["id"], loc.json()["id"]


def _on_hand(client: httpx.Client, h: dict[str, str], location_id: str, item_id: str) -> Decimal:
    rows = client.get(f"/api/inventory?location_id={location_id}", headers=h).json()["items"]
    match = next((r for r in rows if r["item_id"] == item_id), None)
    return Decimal(str(match["quantity"])) if match else Decimal("0")


def _log(client: httpx.Client, h: dict[str, str]) -> list[dict]:
    return client.get("/api/inventory/transactions", headers=h).json()["transactions"]


def test_reversing_an_addition_zeroes_it_out_and_both_entries_stay_in_the_log(client: httpx.Client) -> None:
    h = _headers(client)
    item_id, location_id = _seed(client, h)

    add = client.post(
        "/api/inventory/add",
        json={"item_id": item_id, "location_id": location_id, "quantity": 5, "unit": "each"},
        headers=h,
    )
    assert add.status_code == 200, add.text
    tx_id = add.json()["transaction_ids"][0]
    assert _on_hand(client, h, location_id, item_id) == Decimal("5")

    resp = client.post(f"/api/inventory/transactions/{tx_id}/reverse", headers=h)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["reversed_id"] == tx_id
    reversal_id = body["transaction_id"]
    assert reversal_id != tx_id

    assert _on_hand(client, h, location_id, item_id) == Decimal("0")

    entries = {row["transaction_id"]: row for row in _log(client, h)}
    assert tx_id in entries
    assert reversal_id in entries
    assert entries[reversal_id]["reason"] == "reversal"
    assert entries[reversal_id]["note"] == f"reversal_of:{tx_id}"


def test_reversing_the_same_transaction_twice_conflicts(client: httpx.Client) -> None:
    h = _headers(client)
    item_id, location_id = _seed(client, h)

    add = client.post(
        "/api/inventory/add",
        json={"item_id": item_id, "location_id": location_id, "quantity": 3, "unit": "each"},
        headers=h,
    )
    tx_id = add.json()["transaction_ids"][0]

    first = client.post(f"/api/inventory/transactions/{tx_id}/reverse", headers=h)
    assert first.status_code == 200, first.text

    second = client.post(f"/api/inventory/transactions/{tx_id}/reverse", headers=h)
    assert second.status_code == 409, second.text


def test_reversing_a_reversal_conflicts(client: httpx.Client) -> None:
    h = _headers(client)
    item_id, location_id = _seed(client, h)

    add = client.post(
        "/api/inventory/add",
        json={"item_id": item_id, "location_id": location_id, "quantity": 4, "unit": "each"},
        headers=h,
    )
    tx_id = add.json()["transaction_ids"][0]

    reversal = client.post(f"/api/inventory/transactions/{tx_id}/reverse", headers=h)
    reversal_id = reversal.json()["transaction_id"]

    resp = client.post(f"/api/inventory/transactions/{reversal_id}/reverse", headers=h)
    assert resp.status_code == 409, resp.text


def test_reversing_an_addition_after_partial_consumption_conflicts_with_a_clear_message(
    client: httpx.Client,
) -> None:
    h = _headers(client)
    item_id, location_id = _seed(client, h)

    add = client.post(
        "/api/inventory/add",
        json={"item_id": item_id, "location_id": location_id, "quantity": 5, "unit": "each"},
        headers=h,
    )
    tx_id = add.json()["transaction_ids"][0]

    consume = client.post(
        "/api/inventory/remove",
        json={"item_id": item_id, "location_id": location_id, "quantity": 4, "unit": "each"},
        headers=h,
    )
    assert consume.status_code == 200, consume.text
    assert _on_hand(client, h, location_id, item_id) == Decimal("1")

    resp = client.post(f"/api/inventory/transactions/{tx_id}/reverse", headers=h)
    assert resp.status_code == 409, resp.text
    detail = resp.json()["detail"]
    assert "1" in detail
    assert "left at" in detail

    # Untouched — a failed reversal doesn't mutate stock.
    assert _on_hand(client, h, location_id, item_id) == Decimal("1")


def test_reversing_a_removal_restores_stock(client: httpx.Client) -> None:
    h = _headers(client)
    item_id, location_id = _seed(client, h)

    client.post(
        "/api/inventory/add",
        json={"item_id": item_id, "location_id": location_id, "quantity": 10, "unit": "each"},
        headers=h,
    )
    remove = client.post(
        "/api/inventory/remove",
        json={"item_id": item_id, "location_id": location_id, "quantity": 6, "unit": "each"},
        headers=h,
    )
    assert remove.status_code == 200, remove.text
    remove_tx_id = remove.json()["transaction_ids"][0]
    assert _on_hand(client, h, location_id, item_id) == Decimal("4")

    resp = client.post(f"/api/inventory/transactions/{remove_tx_id}/reverse", headers=h)
    assert resp.status_code == 200, resp.text
    assert resp.json()["reversed_id"] == remove_tx_id

    assert _on_hand(client, h, location_id, item_id) == Decimal("10")


def test_reversing_an_unknown_transaction_is_404(client: httpx.Client) -> None:
    h = _headers(client)
    resp = client.post(
        "/api/inventory/transactions/00000000-0000-0000-0000-000000000000/reverse", headers=h
    )
    assert resp.status_code == 404, resp.text


def test_reversing_an_addition_targets_its_own_lot_not_fefo(client: httpx.Client) -> None:
    """Reversing a newer accidental addition must remove THAT addition's lot —
    never FEFO-consume an older legitimate lot's expiry/price metadata."""
    h = _headers(client)
    item_id, location_id = _seed(client, h)

    old = client.post(
        "/api/inventory/add",
        json={
            "item_id": item_id,
            "location_id": location_id,
            "quantity": 3,
            "unit": "each",
            "expiration_date": "2026-08-15",
        },
        headers=h,
    )
    assert old.status_code == 200, old.text

    mistake = client.post(
        "/api/inventory/add",
        json={
            "item_id": item_id,
            "location_id": location_id,
            "quantity": 4,
            "unit": "each",
            "expiration_date": "2026-12-01",
        },
        headers=h,
    )
    assert mistake.status_code == 200, mistake.text
    mistake_tx_id = mistake.json()["transaction_ids"][0]

    resp = client.post(f"/api/inventory/transactions/{mistake_tx_id}/reverse", headers=h)
    assert resp.status_code == 200, resp.text

    # The older, sooner-expiring lot survives untouched; the mistake's lot is
    # gone. Under FEFO the 2026-08-15 lot would have been eaten first — that is
    # exactly the metadata corruption the targeted reversal prevents.
    lots = client.get(
        f"/api/inventory/lots?item_id={item_id}&location_id={location_id}", headers=h
    ).json()["lots"]
    live = [l for l in lots if Decimal(str(l["quantity_remaining"])) > 0]
    assert len(live) == 1, lots
    assert live[0]["expiration_date"] == "2026-08-15"
    assert Decimal(str(live[0]["quantity_remaining"])) == Decimal("3")
    assert _on_hand(client, h, location_id, item_id) == Decimal("3")
