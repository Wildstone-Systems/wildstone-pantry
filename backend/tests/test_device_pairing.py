"""Device pairing + device-key auth (ADR 0018 §8)."""

from __future__ import annotations

import httpx


def _admin_headers(client: httpx.Client) -> dict[str, str]:
    email, password = "device-admin@example.com", "DevicePass9!"
    client.post("/api/auth/bootstrap-admin", json={"email": email, "password": password})
    login = client.post("/api/auth/login", json={"email": email, "password": password})
    return {"Authorization": f"Bearer {login.json()['token']}"}


def test_pairing_flow_end_to_end(client: httpx.Client) -> None:
    headers = _admin_headers(client)

    minted = client.post("/api/devices/pairing-codes", headers=headers)
    assert minted.status_code == 200, minted.text
    code = minted.json()["code"]

    paired = client.post("/api/devices/pair", json={"code": code, "device_name": "Kitchen iPad"})
    assert paired.status_code == 200, paired.text
    device_key = paired.json()["device_key"]
    assert device_key.startswith("dk_")

    # The one-time code is spent.
    again = client.post("/api/devices/pair", json={"code": code, "device_name": "Sneaky"})
    assert again.status_code == 401

    # The device key authenticates as its bound user via plain bearer auth.
    me = client.get("/api/auth/me", headers={"Authorization": f"Bearer {device_key}"})
    assert me.status_code == 200
    assert me.json()["email"] == "device-admin@example.com"

    # Admin sees and revokes it; the key dies immediately.
    devices = client.get("/api/devices", headers=headers).json()["devices"]
    assert devices[0]["name"] == "Kitchen iPad"
    revoked = client.delete(f"/api/devices/{devices[0]['id']}", headers=headers)
    assert revoked.status_code == 200
    dead = client.get("/api/auth/me", headers={"Authorization": f"Bearer {device_key}"})
    assert dead.status_code == 401


def test_bad_pairing_codes_are_rate_limited(client: httpx.Client) -> None:
    from app.core.ratelimit import login_limiter

    # The limiter is process-global; start this test with a clean budget.
    login_limiter.record_success("pairing:testclient")

    for _ in range(login_limiter.max_failures):
        assert client.post("/api/devices/pair", json={"code": "000000-ffffff"}).status_code == 401
    assert client.post("/api/devices/pair", json={"code": "000000-ffffff"}).status_code == 429


def test_minimal_user_management(client: httpx.Client) -> None:
    headers = _admin_headers(client)

    created = client.post(
        "/api/auth/users",
        json={"email": "sam@example.com", "password": "SamPass99!", "role": "member"},
        headers=headers,
    )
    assert created.status_code == 200, created.text
    assert created.json()["role"] == "member"

    duplicate = client.post(
        "/api/auth/users",
        json={"email": "sam@example.com", "password": "SamPass99!"},
        headers=headers,
    )
    assert duplicate.status_code == 409

    login = client.post("/api/auth/login", json={"email": "sam@example.com", "password": "SamPass99!"})
    assert login.status_code == 200

    # Admin resets the password; member can change their own.
    user_id = created.json()["id"]
    reset = client.post(f"/api/auth/users/{user_id}/password", json={"password": "NewPass123!"}, headers=headers)
    assert reset.status_code == 200
    relogin = client.post("/api/auth/login", json={"email": "sam@example.com", "password": "NewPass123!"})
    assert relogin.status_code == 200
    member_headers = {"Authorization": f"Bearer {relogin.json()['token']}"}
    own = client.post("/api/auth/password", json={"password": "MyOwnPass77!"}, headers=member_headers)
    assert own.status_code == 200
    assert client.post("/api/auth/login", json={"email": "sam@example.com", "password": "MyOwnPass77!"}).status_code == 200

    # Short passwords are rejected cleanly.
    assert client.post("/api/auth/password", json={"password": "short"}, headers=member_headers).status_code in {400, 401}
