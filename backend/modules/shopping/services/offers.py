"""Shopping-list offer search (ADR 0013, Phase 1).

Given a need ("spaghetti sauce"), search the integrations we have for real
purchasable options. This is the third connector role alongside OUT
(ShoppingListTarget) and IN (PurchaseConnector).

Phase 1 ships one free provider — a food-DB search over USDA (and later OFF).
Non-food (paper towels) and priced retailer offers arrive with the Kroger
provider in a later phase; the interface is fixed here so they slot in.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from decimal import Decimal
from typing import Protocol, runtime_checkable

from sqlalchemy.orm import Session

from app.services import provider_keys
from modules.catalog.services.barcode_providers import ProviderRateLimited, search_usda_products
from modules.catalog.services.kroger import search_kroger_offers


@dataclass(frozen=True)
class Offer:
    provider: str
    name: str
    vendor: str | None = None
    vendor_sku: str | None = None
    upc: str | None = None
    brand: str | None = None
    size: str | None = None
    price: Decimal | None = None
    image_url: str | None = None

    def as_dict(self) -> dict:
        d = asdict(self)
        d["price"] = None if self.price is None else float(self.price)
        return d


@runtime_checkable
class OfferSearchProvider(Protocol):
    key: str
    label: str

    def configured(self) -> bool: ...

    def search(self, query: str, *, limit: int = 10, db: Session | None = None) -> list[Offer]: ...


class FoodDatabaseSearch:
    """Free food-database offers (USDA branded foods). No price, no cart.

    Resolves the USDA key DB-override-over-env (ADR 0009), so an admin-set key or
    the environment both work; empty when neither is set."""

    key = "food_db"
    label = "Food database (USDA)"

    def configured(self) -> bool:
        return True  # query-time self-disables when no key; always safe to list

    def search(self, query: str, *, limit: int = 10, db: Session | None = None) -> list[Offer]:
        return [
            Offer(
                provider=self.key,
                name=row["name"],
                upc=row.get("upc"),
                brand=row.get("brand"),
                size=row.get("size"),
            )
            for row in search_usda_products(query, limit=limit, api_key=provider_keys.usda_api_key(db))
        ]


class KrogerSearch:
    """Kroger catalog offers — household + food, with prices when a store is set.

    Resolves credentials DB-override-over-env (ADR 0009); dormant (empty, no HTTP)
    until a client id/secret are configured."""

    key = "kroger"
    label = "Kroger"

    def configured(self) -> bool:
        return True  # query-time self-disables without credentials

    def search(self, query: str, *, limit: int = 10, db: Session | None = None) -> list[Offer]:
        client_id, client_secret = provider_keys.kroger_credentials(db)
        location_id = provider_keys.kroger_location_id(db)
        token_override = None
        if not (client_id and client_secret) and db is not None:
            # No BYO credentials: a brokered instance searches with a
            # broker-minted service token (ADR 0018 §3); None degrades quietly.
            from app.services.broker_connections import service_search_token

            token_override = service_search_token(db)
        rows = search_kroger_offers(
            query,
            limit=limit,
            client_id=client_id,
            client_secret=client_secret,
            location_id=location_id or None,
            token_override=token_override,
        )
        return [
            Offer(
                provider="kroger",
                name=row["name"],
                vendor="kroger",
                vendor_sku=row.get("vendor_sku"),
                upc=row.get("upc"),
                brand=row.get("brand"),
                size=row.get("size"),
                price=Decimal(str(row["price"])) if row.get("price") is not None else None,
                image_url=row.get("image_url"),
            )
            for row in rows
        ]


_PROVIDERS: list[OfferSearchProvider] = [FoodDatabaseSearch(), KrogerSearch()]


def list_offer_providers() -> list[OfferSearchProvider]:
    return list(_PROVIDERS)


def search_offers(query: str, *, limit: int = 10, db: Session | None = None, provider: str | None = None) -> dict:
    """Fan out to every configured provider and merge. A rate-limited provider is
    skipped (not fatal) and reported, so partial results still come back.

    ``provider`` (e.g. "kroger") restricts the search to one source."""
    query = (query or "").strip()
    if not query:
        return {"query": query, "offers": [], "throttled": []}

    wanted = (provider or "").strip() or None
    offers: list[dict] = []
    throttled: list[str] = []
    for provider_impl in _PROVIDERS:
        if wanted and provider_impl.key != wanted:
            continue
        if not provider_impl.configured():
            continue
        try:
            offers.extend(o.as_dict() for o in provider_impl.search(query, limit=limit, db=db))
        except ProviderRateLimited:
            throttled.append(provider_impl.key)

    return {"query": query, "offers": offers, "throttled": throttled}
