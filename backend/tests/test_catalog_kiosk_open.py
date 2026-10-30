"""Catalog is reachable from the kiosk without a login (ADR 0004).

The kiosk UI mounts /catalog outside AdminGuard, so a wall-mounted tablet can
add an item or a location with nobody logged in. The catalog routers therefore
use ``allow_kiosk`` rather than ``require_user``: no credential means the
personal identity, but a credential that *is* presented is still validated.
"""

from __future__ import annotations

import httpx

from app.core.config import settings


def test_catalog_writes_work_without_a_token_while_auth_is_enabled(
    client: httpx.Client, monkeypatch
) -> None:
    monkeypatch.setattr(settings, "auth_enabled", True)

    # Admin surfaces still demand a real token — the kiosk identity is not a way in.
    assert client.get("/api/auth/users").status_code == 401

    location = client.post("/api/locations", json={"name": "Pantry"})
    assert location.status_code == 201, location.text

    item = client.post("/api/items", json={"name": "Olive Oil"})
    assert item.status_code == 201, item.text

    listed = client.get("/api/items")
    assert listed.status_code == 200
    assert any(row["name"] == "Olive Oil" for row in listed.json()["items"])


def test_catalog_still_rejects_a_bad_token_rather_than_downgrading(
    client: httpx.Client, monkeypatch
) -> None:
    """A presented-but-invalid credential is an error, not a silent kiosk fallback."""

    monkeypatch.setattr(settings, "auth_enabled", True)

    resp = client.post(
        "/api/items",
        json={"name": "Rice"},
        headers={"Authorization": "Bearer not-a-real-token"},
    )
    assert resp.status_code == 401
