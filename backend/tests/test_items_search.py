"""`GET /api/items?search=` narrows the catalog.

Previously the route took only `include_inactive`. FastAPI drops unknown query
params silently, so `?search=chicken` was not ignored-then-mismatched — it was never
parsed at all, and every caller got the whole catalog back with no error to notice.
"""

from __future__ import annotations

import httpx


def _names(resp: httpx.Response) -> set[str]:
    return {i["name"] for i in resp.json()["items"]}


def test_search_narrows_to_matching_items(client: httpx.Client) -> None:
    for name in ("Chicken Thighs", "Chicken Stock", "Basmati Rice"):
        client.post("/api/items", json={"name": name})

    assert _names(client.get("/api/items", params={"search": "chicken"})) == {
        "Chicken Thighs",
        "Chicken Stock",
    }


def test_search_is_case_insensitive_and_matches_substrings(client: httpx.Client) -> None:
    client.post("/api/items", json={"name": "Extra Virgin Olive Oil"})
    client.post("/api/items", json={"name": "Basmati Rice"})

    assert _names(client.get("/api/items", params={"search": "OLIVE"})) == {"Extra Virgin Olive Oil"}


def test_search_matches_brand(client: httpx.Client) -> None:
    client.post("/api/items", json={"name": "Ground Cumin", "brand": "Lawry's"})
    client.post("/api/items", json={"name": "Basmati Rice"})

    assert _names(client.get("/api/items", params={"search": "lawry"})) == {"Ground Cumin"}


def test_no_search_term_returns_the_full_catalog(client: httpx.Client) -> None:
    client.post("/api/items", json={"name": "Chicken Thighs"})
    client.post("/api/items", json={"name": "Basmati Rice"})

    assert _names(client.get("/api/items")) == _names(client.get("/api/items", params={"search": "  "}))


def test_no_match_returns_empty_rather_than_everything(client: httpx.Client) -> None:
    client.post("/api/items", json={"name": "Basmati Rice"})

    assert client.get("/api/items", params={"search": "zzzznope"}).json()["items"] == []


def test_like_wildcards_in_the_term_are_literal(client: httpx.Client) -> None:
    """A bare `%` must not behave as match-everything."""
    client.post("/api/items", json={"name": "Basmati Rice"})

    assert client.get("/api/items", params={"search": "%"}).json()["items"] == []
