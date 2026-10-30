from __future__ import annotations

import os

import httpx


API_BASE = os.getenv("API_BASE_URL", "http://localhost:8000")


def _auth_headers() -> dict[str, str]:
    email = os.getenv("TEST_ADMIN_EMAIL", "admin@pantryos.local")
    password = os.getenv("TEST_ADMIN_PASSWORD", "TestPass123!")
    httpx.post(f"{API_BASE}/api/auth/bootstrap-admin", json={"email": email, "password": password}, timeout=10)
    login = httpx.post(f"{API_BASE}/api/auth/login", json={"email": email, "password": password}, timeout=10)
    assert login.status_code == 200
    headers = {"Authorization": f"Bearer {login.json()['token']}"}
    return headers


def test_phase12_feedback_create_list_resolve() -> None:
    headers = _auth_headers()

    created = httpx.post(
        f"{API_BASE}/api/feedback",
        headers=headers,
        json={"category": "bug", "route": "/recipes", "message": "Input loses focus while typing."},
        timeout=10,
    )
    assert created.status_code == 200
    feedback_id = created.json()["id"]

    listed = httpx.get(f"{API_BASE}/api/feedback?limit=50", headers=headers, timeout=10)
    assert listed.status_code == 200
    ids = [row["id"] for row in listed.json()["feedback"]]
    assert feedback_id in ids

    listed_open = httpx.get(f"{API_BASE}/api/feedback?limit=50&status=open", headers=headers, timeout=10)
    assert listed_open.status_code == 200
    ids_open = [row["id"] for row in listed_open.json()["feedback"]]
    assert feedback_id in ids_open

    resolved = httpx.patch(
        f"{API_BASE}/api/feedback/{feedback_id}",
        headers=headers,
        json={"status": "resolved"},
        timeout=10,
    )
    assert resolved.status_code == 200
    assert resolved.json()["status"] == "resolved"

    listed_open_after = httpx.get(f"{API_BASE}/api/feedback?limit=50&status=open", headers=headers, timeout=10)
    assert listed_open_after.status_code == 200
    ids_open_after = [row["id"] for row in listed_open_after.json()["feedback"]]
    assert feedback_id not in ids_open_after

    listed_resolved = httpx.get(f"{API_BASE}/api/feedback?limit=50&status=resolved", headers=headers, timeout=10)
    assert listed_resolved.status_code == 200
    ids_resolved = [row["id"] for row in listed_resolved.json()["feedback"]]
    assert feedback_id in ids_resolved
