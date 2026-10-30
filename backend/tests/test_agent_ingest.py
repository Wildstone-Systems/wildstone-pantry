"""Agent product-URL ingest: URL -> catalog item -> shopping list (+ inventory)."""

from __future__ import annotations

import os
import uuid

import httpx
import pytest

API_BASE = os.getenv("API_BASE_URL", "http://localhost:8000")

_JSONLD_PAGE = """
<html><head>
<script type="application/ld+json">
{"@context":"https://schema.org","@type":"Product","name":"Organic Peanut Butter",
 "brand":{"@type":"Brand","name":"NuttyCo"},"gtin13":"0123456789012",
 "image":"https://example.com/pb.jpg",
 "offers":{"@type":"Offer","price":"5.49","priceCurrency":"USD"}}
</script>
</head><body><h1>Organic Peanut Butter</h1></body></html>
"""

_OG_PAGE = """
<html><head>
<meta property="og:title" content="Sourdough Loaf" />
<meta property="og:image" content="https://example.com/bread.jpg" />
<meta property="product:price:amount" content="4.00" />
</head><body>bread</body></html>
"""


def _auth_headers() -> dict[str, str]:
    email = os.getenv("TEST_ADMIN_EMAIL", "admin@pantryos.local")
    password = os.getenv("TEST_ADMIN_PASSWORD", "TestPass123!")
    httpx.post(f"{API_BASE}/api/auth/bootstrap-admin", json={"email": email, "password": password}, timeout=10)
    login = httpx.post(f"{API_BASE}/api/auth/login", json={"email": email, "password": password}, timeout=10)
    assert login.status_code == 200
    headers = {"Authorization": f"Bearer {login.json()['token']}"}
    return headers


@pytest.fixture
def _patch_fetch(monkeypatch):
    def _install(html: str):
        monkeypatch.setattr("modules.agent.services.product_url.fetch_html", lambda url: html)

    return _install


def test_ingest_jsonld_creates_item_and_shopping_need(_patch_fetch) -> None:
    headers = _auth_headers()
    _patch_fetch(_JSONLD_PAGE)

    resp = httpx.post(
        f"{API_BASE}/api/agent/ingest-product-url",
        json={"url": "https://shop.example.com/pb"},
        headers=headers,
        timeout=10,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["item_name"] == "Organic Peanut Butter"
    assert body["barcode"] == "0123456789012"
    assert body["extracted"]["brand"] == "NuttyCo"
    assert body["extracted"]["price"] == "5.49"
    assert body["shopping_item_id"] is not None
    assert body["inventory_added"] is False

    # The need is on the shopping list, sourced from the agent.
    needs = httpx.get(f"{API_BASE}/api/shopping-list", headers=headers, timeout=10).json()["items"]
    match = [n for n in needs if n["item_id"] == body["item_id"]]
    assert match and match[0]["source"] == "agent"

    # Re-ingesting the same product dedupes onto the same catalog item.
    again = httpx.post(
        f"{API_BASE}/api/agent/ingest-product-url",
        json={"url": "https://shop.example.com/pb"},
        headers=headers,
        timeout=10,
    )
    assert again.json()["item_id"] == body["item_id"]


def test_ingest_with_inventory_placement(_patch_fetch) -> None:
    headers = _auth_headers()
    _patch_fetch(_OG_PAGE)
    suffix = uuid.uuid4().hex[:8]
    loc = httpx.post(f"{API_BASE}/api/locations", json={"name": f"Kitchen {suffix}"}, headers=headers, timeout=10)
    location_id = loc.json()["id"]

    resp = httpx.post(
        f"{API_BASE}/api/agent/ingest-product-url",
        json={
            "url": "https://bakery.example.com/loaf",
            "add_to_shopping": False,
            "add_to_inventory": True,
            "location_id": location_id,
            "quantity": 2,
            "unit": "loaf",
        },
        headers=headers,
        timeout=10,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["item_name"] == "Sourdough Loaf"
    assert body["inventory_added"] is True

    inv = httpx.get(f"{API_BASE}/api/inventory?location_id={location_id}", headers=headers, timeout=10).json()
    assert any(row["item_id"] == body["item_id"] for row in inv["items"])


def test_ingest_requires_location_for_inventory(_patch_fetch) -> None:
    headers = _auth_headers()
    _patch_fetch(_OG_PAGE)
    resp = httpx.post(
        f"{API_BASE}/api/agent/ingest-product-url",
        json={"url": "https://x.example.com/y", "add_to_inventory": True},
        headers=headers,
        timeout=10,
    )
    assert resp.status_code == 400


def test_ingest_rejects_non_http_url() -> None:
    headers = _auth_headers()
    resp = httpx.post(
        f"{API_BASE}/api/agent/ingest-product-url",
        json={"url": "ftp://nope"},
        headers=headers,
        timeout=10,
    )
    assert resp.status_code == 400


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/x",
        "http://localhost/x",
        "http://10.0.0.5/x",
        "http://169.254.169.254/latest/meta-data",  # cloud metadata
        "http://[::1]/x",
        "http://192.168.1.1/x",
    ],
)
def test_ssrf_guard_blocks_internal_addresses(url) -> None:
    from modules.agent.services.product_url import assert_public_url

    with pytest.raises(ValueError):
        assert_public_url(url)


def test_ssrf_guard_allows_public_ip() -> None:
    # Numeric public IP — resolved without DNS, must not be blocked.
    from modules.agent.services.product_url import assert_public_url

    assert_public_url("http://8.8.8.8/")


def test_ingest_endpoint_blocks_internal_url() -> None:
    # No fetch patch: the SSRF guard runs and rejects a loopback target -> 400.
    headers = _auth_headers()
    resp = httpx.post(
        f"{API_BASE}/api/agent/ingest-product-url",
        json={"url": "http://127.0.0.1:9/product"},
        headers=headers,
        timeout=10,
    )
    assert resp.status_code == 400
