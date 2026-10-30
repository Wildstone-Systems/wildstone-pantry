"""Household profiles + kiosk event attribution — ADR 0004 (step 2a).

Profiles are persons (no login). Reads are OPEN so the kiosk can show them;
creates/updates are ADMIN-gated. The selected profile is stamped as the ``actor``
on inventory events via the ``X-Household-Profile`` header.
"""

from __future__ import annotations

import httpx
from sqlalchemy import select

from app.db.session import SessionLocal
from modules.inventory.models import InventoryEvent


def _admin_headers(client: httpx.Client) -> dict[str, str]:
    client.post("/api/auth/bootstrap-admin", json={"email": "admin@pantryos.local", "password": "TestPass123!"})
    login = client.post("/api/auth/login", json={"email": "admin@pantryos.local", "password": "TestPass123!"})
    assert login.status_code == 200
    return {"Authorization": f"Bearer {login.json()['token']}"}


def test_profile_reads_are_open_but_writes_are_admin_gated(client: httpx.Client) -> None:
    # Reads work with no auth at all (kiosk surface).
    empty = client.get("/api/household/profiles")
    assert empty.status_code == 200
    assert empty.json()["profiles"] == []

    # Creating a profile is gated (the protected admin surface): no token -> 401.
    denied = client.post("/api/household/profiles", json={"display_name": "Kiddo"})
    assert denied.status_code == 401

    headers = _admin_headers(client)
    created = client.post(
        "/api/household/profiles",
        json={"display_name": "Kiddo", "daily_calorie_target": 1600},
        headers=headers,
    )
    assert created.status_code == 201
    body = created.json()
    assert body["display_name"] == "Kiddo"
    assert body["account_id"] is None
    assert body["is_active"] is True

    # Now the open list shows the person.
    listed = client.get("/api/household/profiles")
    assert [p["display_name"] for p in listed.json()["profiles"]] == ["Kiddo"]


def test_deactivating_a_profile_hides_it_from_the_default_list(client: httpx.Client) -> None:
    headers = _admin_headers(client)
    pid = client.post("/api/household/profiles", json={"display_name": "Guest"}, headers=headers).json()["id"]

    patched = client.patch(f"/api/household/profiles/{pid}", json={"is_active": False}, headers=headers)
    assert patched.status_code == 200
    assert patched.json()["is_active"] is False

    assert client.get("/api/household/profiles").json()["profiles"] == []
    assert len(client.get("/api/household/profiles?include_inactive=true").json()["profiles"]) == 1


def test_inventory_events_are_attributed_to_the_selected_profile(client: httpx.Client) -> None:
    headers = _admin_headers(client)
    profile_id = client.post(
        "/api/household/profiles", json={"display_name": "Alex"}, headers=headers
    ).json()["id"]

    item_id = client.post("/api/items", json={"name": "Attr Milk"}, headers=headers).json()["id"]
    location_id = client.post("/api/locations", json={"name": "Attr Pantry"}, headers=headers).json()["id"]

    # Kiosk add with the profile header -> event actor is the profile id.
    client.post(
        "/api/inventory/add",
        json={"item_id": item_id, "location_id": location_id, "quantity": 4, "unit": "each"},
        headers={**headers, "X-Household-Profile": profile_id},
    )
    # Add with no profile header -> actor stays null (still succeeds).
    client.post(
        "/api/inventory/add",
        json={"item_id": item_id, "location_id": location_id, "quantity": 1, "unit": "each"},
        headers=headers,
    )

    with SessionLocal() as db:
        events = db.execute(select(InventoryEvent).order_by(InventoryEvent.seq)).scalars().all()
        assert [e.actor for e in events] == [profile_id, None]


def test_admin_writes_require_login_even_in_kiosk_mode(client: httpx.Client, monkeypatch) -> None:
    """The linchpin: in kiosk mode (AUTH_ENABLED=false) everyday endpoints are
    open, but the admin surface must STILL require a real admin login."""
    from app.core.config import settings

    monkeypatch.setattr(settings, "auth_enabled", False)

    # Kiosk everyday op works with no token...
    item_id = client.post("/api/items", json={"name": "Kiosk Milk"}).json()["id"]
    location_id = client.post("/api/locations", json={"name": "Kiosk Pantry"}).json()["id"]
    add = client.post(
        "/api/inventory/add",
        json={"item_id": item_id, "location_id": location_id, "quantity": 1, "unit": "each"},
    )
    assert add.status_code == 200

    # ...but creating a profile (admin surface) is still rejected without a login.
    denied = client.post("/api/household/profiles", json={"display_name": "Kid"})
    assert denied.status_code == 401
    # Reads stay open on the kiosk.
    assert client.get("/api/household/profiles").status_code == 200


def test_profile_pin_set_by_admin_and_verified_openly(client: httpx.Client) -> None:
    headers = _admin_headers(client)
    pid = client.post("/api/household/profiles", json={"display_name": "Sam"}, headers=headers).json()["id"]

    # No PIN yet: has_pin false, and verify is trivially true (nothing to check).
    assert client.get("/api/household/profiles").json()["profiles"][0]["has_pin"] is False

    # Setting a PIN is admin-gated.
    assert client.put(f"/api/household/profiles/{pid}/pin", json={"pin": "1234"}).status_code == 401
    set_resp = client.put(f"/api/household/profiles/{pid}/pin", json={"pin": "1234"}, headers=headers)
    assert set_resp.status_code == 200
    assert set_resp.json()["has_pin"] is True

    # Verifying is OPEN (the kiosk unlock) and the hash is never exposed.
    assert "pin_hash" not in set_resp.json()
    assert client.post(f"/api/household/profiles/{pid}/verify-pin", json={"pin": "1234"}).json()["valid"] is True
    assert client.post(f"/api/household/profiles/{pid}/verify-pin", json={"pin": "9999"}).json()["valid"] is False

    # Bad PIN format rejected; clearing removes it.
    assert client.put(f"/api/household/profiles/{pid}/pin", json={"pin": "ab"}, headers=headers).status_code == 400
    cleared = client.put(f"/api/household/profiles/{pid}/pin", json={"pin": None}, headers=headers)
    assert cleared.json()["has_pin"] is False


def test_invalid_profile_header_does_not_block_the_write(client: httpx.Client) -> None:
    headers = _admin_headers(client)
    item_id = client.post("/api/items", json={"name": "Robust Milk"}, headers=headers).json()["id"]
    location_id = client.post("/api/locations", json={"name": "Robust Pantry"}, headers=headers).json()["id"]

    resp = client.post(
        "/api/inventory/add",
        json={"item_id": item_id, "location_id": location_id, "quantity": 2, "unit": "each"},
        headers={**headers, "X-Household-Profile": "not-a-uuid"},
    )
    assert resp.status_code == 200
    with SessionLocal() as db:
        actors = [e.actor for e in db.execute(select(InventoryEvent)).scalars().all()]
        assert actors == [None]
