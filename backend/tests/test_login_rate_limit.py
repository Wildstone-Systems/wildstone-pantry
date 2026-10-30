"""Login brute-force throttling (ADR 0018 §1).

Only FAILURES are limited, keyed per (client, email): a burst of bad passwords
for one account 429s, while other accounts — and successful logins — are never
throttled.
"""

from __future__ import annotations

import httpx

from app.core.ratelimit import login_limiter


def _fail_login(client: httpx.Client, email: str) -> httpx.Response:
    return client.post("/api/auth/login", json={"email": email, "password": "wrong-password"})


def test_repeated_failures_throttle_only_that_email(client: httpx.Client) -> None:
    email = "bruteforce-target@example.com"
    for _ in range(login_limiter.max_failures):
        assert _fail_login(client, email).status_code == 401

    throttled = _fail_login(client, email)
    assert throttled.status_code == 429

    # A different account from the same client is unaffected.
    assert _fail_login(client, "someone-else@example.com").status_code == 401


def test_successful_login_clears_the_failure_budget(client: httpx.Client) -> None:
    email = "recovering-admin@example.com"
    password = "CorrectHorse9!"
    boot = client.post("/api/auth/bootstrap-admin", json={"email": email, "password": password})
    assert boot.status_code in {200, 409}

    for _ in range(login_limiter.max_failures - 1):
        assert _fail_login(client, email).status_code == 401

    ok = client.post("/api/auth/login", json={"email": email, "password": password})
    assert ok.status_code == 200

    # The success reset the budget: more failures are tolerated again.
    assert _fail_login(client, email).status_code == 401
