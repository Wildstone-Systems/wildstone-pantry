"""Agent resolve-items: receipt-style lines -> catalog item ids (dry run by default)."""

from __future__ import annotations

import httpx

URL = "/api/agent/resolve-items"


def _admin(client: httpx.Client) -> dict[str, str]:
    client.post("/api/auth/bootstrap-admin", json={"email": "admin@pantryos.local", "password": "TestPass123!"})
    login = client.post("/api/auth/login", json={"email": "admin@pantryos.local", "password": "TestPass123!"})
    return {"Authorization": f"Bearer {login.json()['token']}"}


def _item_count(client: httpx.Client, h: dict[str, str]) -> int:
    return len(client.get("/api/items", headers=h).json()["items"])


def test_requires_auth(client: httpx.Client, monkeypatch) -> None:
    from app.core.config import settings

    monkeypatch.setattr(settings, "auth_enabled", True)
    assert client.post(URL, json={"lines": [{"name": "Milk"}]}).status_code == 401


def test_dry_run_matches_existing_and_writes_nothing(client: httpx.Client) -> None:
    h = _admin(client)
    item = client.post("/api/items", headers=h, json={"name": "Parmesan cheese"}).json()
    before = _item_count(client, h)

    body = client.post(
        URL, headers=h, json={"lines": [{"name": "Kraft Parmesan Cheese 8 oz"}, {"name": "Saffron threads"}]}
    ).json()

    assert _item_count(client, h) == before
    hit, miss = body["items"]
    assert hit["match"] == "existing" and hit["item_id"] == item["id"]
    assert miss["match"] == "not_found" and miss["item_id"] is None
    assert miss["suggested_expiry_days"] > 0
    assert body["created_count"] == 0 and body["not_found_count"] == 1


def test_create_missing_creates_once_per_product(client: httpx.Client) -> None:
    h = _admin(client)
    before = _item_count(client, h)

    body = client.post(
        URL,
        headers=h,
        json={"lines": [{"name": "Saffron threads"}, {"name": "Saffron threads"}], "create_missing": True},
    ).json()

    first, second = body["items"]
    assert first["match"] == "created" and second["item_id"] == first["item_id"]
    assert body["not_found_count"] == 0
    assert _item_count(client, h) == before + 1

    # A second run finds the item instead of creating a duplicate.
    again = client.post(URL, headers=h, json={"lines": [{"name": "Saffron threads"}], "create_missing": True}).json()
    assert again["items"][0]["match"] == "existing" and again["items"][0]["item_id"] == first["item_id"]


def test_barcode_wins_over_name(client: httpx.Client) -> None:
    h = _admin(client)
    item = client.post("/api/items", headers=h, json={"name": "Oat milk", "barcode": "012345678905"}).json()

    body = client.post(URL, headers=h, json={"lines": [{"name": "Store brand drink", "barcode": "012345678905"}]}).json()

    assert body["items"][0]["match"] == "barcode" and body["items"][0]["item_id"] == item["id"]


def test_does_not_touch_stock(client: httpx.Client) -> None:
    h = _admin(client)
    loc = client.post("/api/locations", headers=h, json={"name": "Resolve Pantry"}).json()
    client.post(URL, headers=h, json={"lines": [{"name": "Rice"}], "create_missing": True})
    assert client.get("/api/inventory", headers=h, params={"location_id": loc["id"]}).json()["items"] == []


def test_rejects_empty_and_oversized_batches(client: httpx.Client) -> None:
    h = _admin(client)
    assert client.post(URL, headers=h, json={"lines": []}).status_code == 422
    too_many = [{"name": f"item {i}"} for i in range(101)]
    assert client.post(URL, headers=h, json={"lines": too_many}).status_code == 422
