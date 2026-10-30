"""Turn a product web page into catalog + shopping/inventory entries.

The agent-facing convenience behind ``POST /api/agent/ingest-product-url``:
fetch a product URL, extract structured product data (schema.org JSON-LD, then
OpenGraph as a fallback), upsert a catalog item, and place it on the shopping
list and/or into inventory. Orchestrates catalog / shopping / inventory through
their service interfaces (no cross-module table writes).
"""

from __future__ import annotations

import ipaddress
import json
import socket
from decimal import Decimal
from urllib.parse import urljoin, urlparse
from uuid import UUID

import requests
from bs4 import BeautifulSoup
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Item
from modules.catalog.schemas.item import ItemCreate
from modules.catalog.services.barcodes import find_item_by_name, normalize_barcode
from modules.catalog.services.items import create_item
from modules.inventory.services.inventory import add_inventory
from modules.shopping.services.shopping_list import add_manual_entry

_TIMEOUT = 15
_MAX_REDIRECTS = 5
_USER_AGENT = "PantryOS-Agent/1.0 (+https://github.com/Wildstone-Systems/wildstone-pantry)"
_REDIRECT_STATUSES = {301, 302, 303, 307, 308}


def assert_public_url(url: str) -> None:
    """SSRF guard: only http(s) to a publicly-routable host.

    Resolves the host and rejects loopback / private / link-local / reserved /
    multicast addresses (e.g. localhost, 10.0.0.0/8, 169.254.169.254 metadata).
    """
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ValueError("URL must be http(s)")
    host = parsed.hostname
    if not host:
        raise ValueError("URL has no host")

    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    try:
        infos = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        raise ValueError(f"Could not resolve host: {host}") from exc

    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_reserved
            or ip.is_multicast
            or ip.is_unspecified
        ):
            raise ValueError("Refusing to fetch a non-public address")


def fetch_html(url: str) -> str:
    """Fetch a page's HTML, validating each hop against the SSRF guard.

    Redirects are followed manually so the guard runs on every target (an allowed
    public URL must not be able to bounce us to an internal one). Isolated so
    tests can patch the network out.
    """
    for _ in range(_MAX_REDIRECTS + 1):
        assert_public_url(url)
        resp = requests.get(
            url,
            timeout=_TIMEOUT,
            headers={"User-Agent": _USER_AGENT},
            allow_redirects=False,
        )
        if resp.status_code in _REDIRECT_STATUSES and resp.headers.get("Location"):
            url = urljoin(url, resp.headers["Location"])
            continue
        resp.raise_for_status()
        return resp.text
    raise ValueError("Too many redirects")


def _first(value):
    return value[0] if isinstance(value, list) and value else value


def _iter_products(node):
    """Yield schema.org Product nodes from a parsed JSON-LD value."""
    if isinstance(node, list):
        for item in node:
            yield from _iter_products(item)
    elif isinstance(node, dict):
        if "@graph" in node:
            yield from _iter_products(node["@graph"])
        types = node.get("@type")
        types = types if isinstance(types, list) else [types]
        if any(isinstance(t, str) and t.lower() == "product" for t in types):
            yield node


def _extract_from_jsonld(soup: BeautifulSoup) -> dict:
    for tag in soup.find_all("script", type="application/ld+json"):
        raw = tag.string or tag.text
        if not raw:
            continue
        try:
            data = json.loads(raw)
        except (ValueError, TypeError):
            continue
        for product in _iter_products(data):
            brand = product.get("brand")
            if isinstance(brand, dict):
                brand = brand.get("name")
            image = _first(product.get("image"))
            if isinstance(image, dict):
                image = image.get("url")
            barcode = (
                product.get("gtin13")
                or product.get("gtin12")
                or product.get("gtin8")
                or product.get("gtin")
                or product.get("mpn")
                or product.get("sku")
            )
            offers = _first(product.get("offers")) or {}
            price = offers.get("price") if isinstance(offers, dict) else None
            currency = offers.get("priceCurrency") if isinstance(offers, dict) else None
            name = product.get("name")
            if name:
                return {
                    "name": str(name).strip(),
                    "brand": str(brand).strip() if brand else None,
                    "barcode": str(barcode).strip() if barcode else None,
                    "image_url": str(image).strip() if image else None,
                    "price": str(price) if price is not None else None,
                    "currency": str(currency) if currency else None,
                }
    return {}


def _meta(soup: BeautifulSoup, prop: str) -> str | None:
    tag = soup.find("meta", property=prop) or soup.find("meta", attrs={"name": prop})
    content = tag.get("content") if tag else None
    return content.strip() if content else None


def _extract_from_opengraph(soup: BeautifulSoup) -> dict:
    name = _meta(soup, "og:title")
    if not name:
        title = soup.find("title")
        name = title.text.strip() if title and title.text else None
    if not name:
        return {}
    return {
        "name": name,
        "brand": _meta(soup, "og:brand") or _meta(soup, "product:brand"),
        "barcode": _meta(soup, "product:retailer_item_id"),
        "image_url": _meta(soup, "og:image"),
        "price": _meta(soup, "product:price:amount") or _meta(soup, "og:price:amount"),
        "currency": _meta(soup, "product:price:currency") or _meta(soup, "og:price:currency"),
    }


def extract_product(html: str) -> dict:
    """Extract a product from page HTML (JSON-LD first, then OpenGraph)."""
    soup = BeautifulSoup(html, "html.parser")
    product = _extract_from_jsonld(soup) or _extract_from_opengraph(soup)
    if not product.get("name"):
        raise ValueError("Could not extract a product from the page")
    return product


def _upsert_item(db: Session, product: dict) -> Item:
    barcode = product.get("barcode")
    if barcode:
        normalized = normalize_barcode(barcode)
        existing = db.execute(select(Item).where(Item.barcode == normalized)).scalar_one_or_none()
        if existing:
            return existing
    existing_by_name = find_item_by_name(db, product["name"])
    if existing_by_name:
        return existing_by_name
    return create_item(
        db,
        ItemCreate(
            name=product["name"],
            brand=product.get("brand"),
            barcode=barcode,
            image_url=product.get("image_url"),
            source="agent",
        ),
    )


def ingest_product_url(
    db: Session,
    *,
    url: str,
    add_to_shopping: bool = True,
    add_to_inventory: bool = False,
    location_id: UUID | None = None,
    container_id: UUID | None = None,
    quantity: Decimal = Decimal("1"),
    unit: str = "each",
) -> dict:
    if not (url.startswith("http://") or url.startswith("https://")):
        raise ValueError("URL must be http(s)")
    if add_to_inventory and location_id is None:
        raise ValueError("location_id is required to add to inventory")

    product = extract_product(fetch_html(url))
    item = _upsert_item(db, product)

    result: dict = {
        "item_id": item.id,
        "item_name": item.name,
        "barcode": item.barcode,
        "image_url": item.image_url,
        "extracted": product,
        "shopping_item_id": None,
        "inventory_added": False,
    }

    if add_to_shopping:
        need = add_manual_entry(
            db,
            item_id=item.id,
            name=item.name,
            quantity=quantity,
            unit=unit,
            note=url,
            source="agent",
        )
        result["shopping_item_id"] = need["id"]

    if add_to_inventory and location_id is not None:
        add_inventory(
            db,
            item_id=item.id,
            location_id=location_id,
            quantity=quantity,
            unit=unit,
            expiration_date=None,
            reason="agent_ingest",
            note=url,
            container_id=container_id,
        )
        result["inventory_added"] = True

    return result
