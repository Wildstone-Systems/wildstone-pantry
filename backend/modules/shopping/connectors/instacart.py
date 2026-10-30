"""Instacart Developer Platform — shoppable shopping-list link (OUT, ADR 0013 Phase 4b).

The one self-serve path to *ordering across many retailers*: POST a list of
line-items and get back a ``products_link_url`` where the user picks any Instacart
retailer, the items land in a cart, and they check out. Unlike the Kroger Cart
(per-user OAuth), this is a single API key and a link-out — no OAuth, no redirect.

DORMANT / BUILT-TO-SPEC: written to the documented IDP contract
(POST /idp/v1/products/products_link) but **not yet verified against the live
API** — Instacart isn't accepting new developer applications, so no key exists to
test with. It activates the moment an ``instacart`` API key is configured under
Integrations. See docs.instacart.com/developer_platform_api.
"""

from __future__ import annotations

import httpx

from modules.shopping.connectors.base import NotConfiguredError

_PROD_BASE = "https://connect.instacart.com"
_DEV_BASE = "https://connect.dev.instacart.tools"  # for reference; set via base_url override
_PATH = "/idp/v1/products/products_link"
_TIMEOUT = 10.0


class InstacartError(RuntimeError):
    """Instacart link creation failed, with a human-readable reason."""


def create_products_link(
    *,
    title: str,
    line_items: list[dict],
    api_key: str,
    base_url: str | None = None,
) -> str:
    """Create a shoppable-list page and return its ``products_link_url``.

    ``line_items`` are ``{name, quantity, unit, upcs?}`` dicts (name is the only
    required field; a UPC is prioritised when present, else Instacart matches by
    name). Raises NotConfiguredError without a key, InstacartError on API failure.
    """
    if not api_key:
        raise NotConfiguredError("No Instacart API key configured.")

    url = (base_url or _PROD_BASE).rstrip("/") + _PATH
    body = {"title": title, "link_type": "shopping_list", "line_items": line_items}
    resp = httpx.post(
        url,
        json=body,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        timeout=_TIMEOUT,
    )
    if resp.status_code >= 400:
        raise InstacartError(f"Instacart link creation failed ({resp.status_code}): {resp.text[:200]}")
    link = (resp.json() or {}).get("products_link_url")
    if not link:
        raise InstacartError("Instacart returned no products_link_url.")
    return link
