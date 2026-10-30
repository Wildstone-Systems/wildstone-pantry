"""Insights are derived from the event ledger, not the legacy tables (ADR 0002 follow-up)."""

from __future__ import annotations

from datetime import date, timedelta

import httpx


def _headers(client: httpx.Client) -> dict[str, str]:
    client.post("/api/auth/bootstrap-admin", json={"email": "admin@pantryos.local", "password": "TestPass123!"})
    login = client.post("/api/auth/login", json={"email": "admin@pantryos.local", "password": "TestPass123!"})
    return {"Authorization": f"Bearer {login.json()['token']}"}


def test_insights_read_from_ledger_projections(client: httpx.Client) -> None:
    h = _headers(client)
    item = client.post("/api/items", json={"name": "Yogurt"}, headers=h).json()["id"]
    loc = client.post("/api/locations", json={"name": "Fridge"}, headers=h).json()["id"]

    soon = (date.today() + timedelta(days=20)).isoformat()
    # Intake with a near expiry (creates a lot carrying the date), then consume some.
    client.post(
        "/api/inventory/add",
        json={"item_id": item, "location_id": loc, "quantity": 10, "unit": "each", "expiration_date": soon},
        headers=h,
    )
    client.post(
        "/api/inventory/remove",
        json={"item_id": item, "location_id": loc, "quantity": 6, "unit": "each"},
        headers=h,
    )

    # Consumption trend reflects the consumed event against the ledger balance.
    trends = client.get(f"/api/insights/trends/{loc}", headers=h).json()["trends"]
    row = next(t for t in trends if t["item_id"] == item)
    assert row["avg_daily_usage"] > 0

    # Restock suggestion sees the ledger balance (4 -> low stock isn't triggered here,
    # but the endpoint must succeed sourced from inv_balance).
    assert client.get(f"/api/insights/restock/{loc}", headers=h).status_code == 200

    # Expiring reads per-lot dates from inv_lot (the purchased event's lot).
    expiring = client.get(f"/api/insights/expiring/{loc}?within_days=180", headers=h).json()["expiring_items"]
    assert "Yogurt" in [e["item_name"] for e in expiring]
