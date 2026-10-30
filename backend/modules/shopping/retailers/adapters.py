"""Retailer adapters: data-backed link adapters + code-backed API retailers.

A search-link-only retailer is pure data (a ``retailer_links`` row wrapped in
``LinkRetailerAdapter``); Kroger and Instacart overlay API capabilities on
their row. ToS posture (ADR 0008/0019): deep links are data, not scraping —
PantryOS never fetches retailer pages server-side.
"""

from __future__ import annotations

from urllib.parse import quote, quote_plus

from sqlalchemy.orm import Session

from app.models import RetailerLink
from modules.shopping.retailers.base import (
    CAP_CART_ADD,
    CAP_CART_LINK,
    CAP_PRODUCT_LINK,
    CAP_PRODUCT_SEARCH,
    CAP_SEARCH_LINK,
)


def _encode(query: str, encoder: str) -> str:
    return quote(query, safe="") if encoder == "percent" else quote_plus(query)


def fallback_search_url(domain: str, query: str) -> str:
    """Search-engine site-search — effectively unbreakable, no consent walls,
    so a stale template degrades the experience without ever dead-ending it."""
    return f"https://duckduckgo.com/?q={quote_plus(f'site:{domain} {query}')}"


class LinkRetailerAdapter:
    """A retailer whose capabilities come entirely from its data row."""

    def __init__(self, row: RetailerLink) -> None:
        self._row = row
        self.key = row.key
        self.label = row.label

    def capabilities(self) -> set[str]:
        caps = {CAP_SEARCH_LINK}
        if self._row.product_template:
            caps.add(CAP_PRODUCT_LINK)
        return caps

    def available(self, db: Session) -> set[str]:
        # Explicitly the LINK capabilities (not self.capabilities(), which a
        # subclass widens): links need no credentials, API capabilities do.
        return LinkRetailerAdapter.capabilities(self)

    def search_url(self, db: Session, query: str) -> str | None:
        row = self._row
        # 404/redirect-home = broken; 403 is a bot wall and stays usable.
        if row.search_template and row.status != "broken":
            return row.search_template.replace("{q}", _encode(query, row.encoder))
        return fallback_search_url(row.domain, query)

    def product_url(self, db: Session, external_id: str) -> str | None:
        if self._row.product_template:
            return self._row.product_template.replace("{id}", _encode(external_id, "percent"))
        return None


class KrogerAdapter(LinkRetailerAdapter):
    """Kroger: the one retailer with an indie-accessible cart API. Search and
    cart light up with BYO credentials OR a brokered connection (ADR 0018 §3) —
    both declare identical capabilities; credential mode is orthogonal."""

    def capabilities(self) -> set[str]:
        return super().capabilities() | {CAP_PRODUCT_SEARCH, CAP_CART_ADD}

    def available(self, db: Session) -> set[str]:
        caps = super().available(db)
        from app.services import broker_connections, provider_keys

        client_id, client_secret = provider_keys.kroger_credentials(db)
        if (client_id and client_secret) or broker_connections.get_connection(db, "kroger") is not None:
            caps |= {CAP_PRODUCT_SEARCH, CAP_CART_ADD}
        return caps


class InstacartAdapter(LinkRetailerAdapter):
    """Instacart: shoppable-list links via the Developer Platform API key
    (dormant — IDP applications closed as of 2026-07-30, ADR 0019)."""

    def capabilities(self) -> set[str]:
        return super().capabilities() | {CAP_CART_LINK}

    def available(self, db: Session) -> set[str]:
        caps = super().available(db)
        from app.services import provider_keys

        if provider_keys.instacart_api_key(db):
            caps |= {CAP_CART_LINK}
        return caps


# Code-backed adapters, keyed by the retailer_links row they overlay.
CODE_ADAPTERS: dict[str, type[LinkRetailerAdapter]] = {
    "kroger": KrogerAdapter,
    "instacart": InstacartAdapter,
}
