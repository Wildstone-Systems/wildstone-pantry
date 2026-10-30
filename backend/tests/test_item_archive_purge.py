"""Deleting an item archives it; purging is the deliberate, confirmed exception.

The behaviour under test exists because the old hard delete cascaded into
`inventory_events` and silently took an item's ledger history with it. In an
append-only store (ADR 0002) that isn't a delete, it's a rewrite of the past.
"""

from __future__ import annotations

import uuid

import pytest


@pytest.fixture()
def stocked_item(client):
    """An item with real history — the case a hard delete used to destroy."""
    loc = client.post("/api/locations", json={"name": f"Pantry {uuid.uuid4().hex[:6]}"}).json()
    item = client.post("/api/items", json={"name": f"Rice {uuid.uuid4().hex[:6]}"}).json()
    client.post(
        "/api/inventory/add",
        json={"item_id": item["id"], "location_id": loc["id"], "quantity": 3, "unit": "each"},
    )
    return item


def _events_for(item_id: str) -> int:
    from app.db import SessionLocal
    from modules.inventory.models.event import InventoryEvent

    db = SessionLocal()
    try:
        return db.query(InventoryEvent).filter_by(item_id=uuid.UUID(item_id)).count()
    finally:
        db.close()


def test_delete_archives_and_keeps_every_ledger_row(client, stocked_item):
    before = _events_for(stocked_item["id"])
    assert before > 0, "fixture should have produced history to protect"

    res = client.delete(f"/api/items/{stocked_item['id']}")
    assert res.status_code == 200, res.text
    assert res.json()["is_active"] is False

    # The whole point: history survives a delete.
    assert _events_for(stocked_item["id"]) == before


def test_an_archived_item_drops_out_of_the_catalogue_but_can_be_found(client, stocked_item):
    client.delete(f"/api/items/{stocked_item['id']}")

    visible = client.get("/api/items", params={"limit": 500}).json()["items"]
    assert all(row["id"] != stocked_item["id"] for row in visible)

    with_archived = client.get(
        "/api/items", params={"limit": 500, "include_inactive": True}
    ).json()["items"]
    assert any(row["id"] == stocked_item["id"] for row in with_archived)


def test_archiving_is_reversible(client, stocked_item):
    client.delete(f"/api/items/{stocked_item['id']}")
    res = client.post(f"/api/items/{stocked_item['id']}/restore")
    assert res.status_code == 200, res.text
    assert res.json()["is_active"] is True

    visible = client.get("/api/items", params={"limit": 500}).json()["items"]
    assert any(row["id"] == stocked_item["id"] for row in visible)


def test_usage_reports_what_a_purge_would_destroy(client, stocked_item):
    usage = client.get(f"/api/items/{stocked_item['id']}/usage").json()
    assert usage["ledger_events"] > 0
    assert usage["on_hand"] == 3
    assert usage["name"] == stocked_item["name"]


def test_purge_refuses_without_the_exact_name(client, stocked_item):
    for wrong in ("", "  ", "not the name", stocked_item["name"].lower() + "x"):
        res = client.request(
            "DELETE",
            f"/api/items/{stocked_item['id']}/purge",
            params={"confirm_name": wrong},
        )
        assert res.status_code == 400, f"{wrong!r} should not have been accepted"

    # Nothing was touched by any of those attempts.
    assert _events_for(stocked_item["id"]) > 0
    assert client.get(f"/api/items/{stocked_item['id']}").status_code == 200


def test_purge_with_the_exact_name_destroys_it_and_says_what_went(client, stocked_item):
    res = client.request(
        "DELETE",
        f"/api/items/{stocked_item['id']}/purge",
        params={"confirm_name": stocked_item["name"]},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["purged"] is True
    assert body["ledger_events"] > 0, "the report must state what it destroyed"

    assert client.get(f"/api/items/{stocked_item['id']}").status_code == 404
    assert _events_for(stocked_item["id"]) == 0


def test_purging_something_that_does_not_exist_is_a_404(client):
    res = client.request(
        "DELETE", f"/api/items/{uuid.uuid4()}/purge", params={"confirm_name": "whatever"}
    )
    assert res.status_code == 404


def test_an_agent_key_cannot_purge_even_with_the_right_name(client):
    """"Only a person" is the point: an agent credential can sit in a config
    file or be driven by a model that mis-reads an instruction. Neither should
    be able to rewrite the ledger, however well-formed the request is."""
    client.post(
        "/api/auth/bootstrap-admin",
        json={"email": "admin@pantryos.local", "password": "TestPass123!"},
    )
    token = client.post(
        "/api/auth/login", json={"email": "admin@pantryos.local", "password": "TestPass123!"}
    ).json()["token"]
    admin = {"Authorization": f"Bearer {token}"}
    key = client.post(
        "/api/agent-keys", json={"name": "purger", "scopes": "read,write"}, headers=admin
    ).json()["secret"]

    item = client.post("/api/items", json={"name": "Agent Bait"}, headers=admin).json()

    res = client.request(
        "DELETE",
        f"/api/items/{item['id']}/purge",
        params={"confirm_name": "Agent Bait"},
        headers={"X-Agent-Key": key},
    )
    assert res.status_code == 403
    assert "person" in res.json()["detail"].lower()

    # Still there. An agent that wants it gone can archive it instead.
    assert client.get(f"/api/items/{item['id']}", headers=admin).status_code == 200
    assert (
        client.delete(f"/api/items/{item['id']}", headers={"X-Agent-Key": key}).status_code == 200
    )
