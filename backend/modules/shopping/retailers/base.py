"""Retailer capability model (ADR 0019 D1).

One retailer = one adapter declaring capability keys. ``capabilities()`` is
static — what the retailer CAN do, never varying per install; ``available(db)``
is the subset usable right now (credentials present, brokered entitlement
live). The UI renders capabilities() and greys out the difference — that's
what lets it say "Kroger: cart (connect to enable)" instead of hiding the
option.

Existing interfaces (``ShoppingListTarget``, ``OfferSearchProvider``, the
Kroger cart service) are the *implementations behind* an adapter's
capabilities; adapters compose them, they don't duplicate them.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from sqlalchemy.orm import Session

CAP_PRODUCT_SEARCH = "product_search"  # API search returning Offers
CAP_PRODUCT_DETAILS = "product_details"
CAP_STORE_SEARCH = "store_search"
CAP_STORE_INVENTORY = "store_inventory"
CAP_PRODUCT_LINK = "product_deep_link"  # stable per-product URL
CAP_SEARCH_LINK = "search_deep_link"  # site-search URL template
CAP_CART_ADD = "cart_add"  # API cart add (checkout handoff)
CAP_CART_LINK = "cart_link"  # shareable pre-filled list/cart URL
CAP_AFFILIATE = "affiliate"  # monetizable links / product feeds
CAP_PURCHASE_HISTORY = "purchase_history"  # future
CAP_RECEIPT_IMPORT = "receipt_import"  # e-receipt parsing (IMAP)


@runtime_checkable
class RetailerAdapter(Protocol):
    key: str  # "kroger", "target", ...
    label: str

    def capabilities(self) -> set[str]:
        """Static: what this retailer CAN do (never varies per install)."""
        ...

    def available(self, db: Session) -> set[str]:
        """Dynamic: the subset usable NOW. available() ⊆ capabilities()."""
        ...

    def search_url(self, db: Session, query: str) -> str | None: ...

    def product_url(self, db: Session, external_id: str) -> str | None: ...
