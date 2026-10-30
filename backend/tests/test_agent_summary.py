"""GET /api/agent/summary — one-call household snapshot for integrations (ADR 0011)."""

from __future__ import annotations

from datetime import date, timedelta

import httpx


def _admin_headers(client: httpx.Client) -> dict[str, str]:
    client.post("/api/auth/bootstrap-admin", json={"email": "admin@pantryos.local", "password": "TestPass123!"})
    login = client.post("/api/auth/login", json={"email": "admin@pantryos.local", "password": "TestPass123!"})
    return {"Authorization": f"Bearer {login.json()['token']}"}


def test_summary_counts(client: httpx.Client) -> None:
    h = _admin_headers(client)

    location_id = client.post("/api/locations", json={"name": "Pantry"}, headers=h).json()["id"]

    # An item expiring soon, and one tracked below its restock threshold.
    soon = client.post("/api/items", json={"name": "Yoghurt"}, headers=h).json()["id"]
    low = client.post("/api/items", json={"name": "Rice", "restock_threshold": 5}, headers=h).json()["id"]

    client.post(
        "/api/inventory/add",
        json={
            "item_id": soon,
            "location_id": location_id,
            "quantity": 2,
            "unit": "each",
            "expiration_date": (date.today() + timedelta(days=2)).isoformat(),
        },
        headers=h,
    )
    client.post(
        "/api/inventory/add",
        json={"item_id": low, "location_id": location_id, "quantity": 1, "unit": "each"},
        headers=h,
    )

    body = client.get("/api/agent/summary", headers=h).json()
    assert body["within_days"] == 7
    assert body["locations"] == 1
    assert body["expiring_soon"] == 1
    assert body["expired"] == 0
    assert body["low_stock_items"] == 1  # Rice: 1 on hand < threshold 5
    assert body["shopping_pending"] >= 1  # the auto restock entry for Rice

    # A tighter window excludes the 2-day expiry.
    body = client.get("/api/agent/summary", params={"within_days": 1}, headers=h).json()
    assert body["expiring_soon"] == 0


def test_summary_requires_auth(client: httpx.Client) -> None:
    assert client.get("/api/agent/summary").status_code == 401


def test_summary_works_with_agent_key_and_marks_it_used(client: httpx.Client) -> None:
    h = _admin_headers(client)
    created = client.post("/api/agent-keys", json={"name": "summary-test"}, headers=h).json()
    assert created["key"]["last_used_at"] is None  # never used at mint time

    resp = client.get("/api/agent/summary", headers={"X-Agent-Key": created["secret"]})
    assert resp.status_code == 200
    assert resp.json()["locations"] == 0

    # Observability: the key now reports when it was last used.
    keys = {k["id"]: k for k in client.get("/api/agent-keys", headers=h).json()["keys"]}
    assert keys[created["key"]["id"]]["last_used_at"] is not None
