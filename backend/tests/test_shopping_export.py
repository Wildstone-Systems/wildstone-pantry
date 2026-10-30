"""Shopping-list export via pluggable targets (step 5a)."""

from __future__ import annotations

import httpx


def _headers(client: httpx.Client) -> dict[str, str]:
    client.post("/api/auth/bootstrap-admin", json={"email": "admin@pantryos.local", "password": "TestPass123!"})
    login = client.post("/api/auth/login", json={"email": "admin@pantryos.local", "password": "TestPass123!"})
    return {"Authorization": f"Bearer {login.json()['token']}"}


def test_export_targets_list_includes_dormant_kroger(client: httpx.Client) -> None:
    h = _headers(client)
    targets = {t["key"]: t for t in client.get("/api/shopping-list/export/targets", headers=h).json()["targets"]}
    assert targets["text"]["configured"] is True
    assert targets["csv"]["configured"] is True
    # Kroger is registered but dormant until credentials are set.
    assert targets["kroger"]["configured"] is False


def test_text_and_csv_export_of_pending_items(client: httpx.Client) -> None:
    h = _headers(client)
    client.post("/api/shopping-list", json={"name": "Milk", "quantity": 2, "unit": "gal"}, headers=h)
    client.post("/api/shopping-list", json={"name": "Eggs", "quantity": 12}, headers=h)

    text = client.get("/api/shopping-list/export?target=text", headers=h).json()
    assert text["format"] == "text"
    assert "Milk" in text["body"] and "2 gal" in text["body"]

    csv = client.get("/api/shopping-list/export?target=csv", headers=h).json()
    assert csv["format"] == "csv"
    assert csv["filename"] == "shopping-list.csv"
    assert csv["body"].splitlines()[0] == "name,quantity,unit"
    assert any(line.startswith("Eggs,12,") for line in csv["body"].splitlines())


def test_dormant_kroger_export_returns_409(client: httpx.Client) -> None:
    h = _headers(client)
    resp = client.get("/api/shopping-list/export?target=kroger", headers=h)
    assert resp.status_code == 409
    assert "not configured" in resp.json()["detail"].lower()


def test_unknown_target_returns_404(client: httpx.Client) -> None:
    h = _headers(client)
    assert client.get("/api/shopping-list/export?target=nope", headers=h).status_code == 404
