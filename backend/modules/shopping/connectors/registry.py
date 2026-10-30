"""Registry of shopping-list export targets (step 5).

Adding a vendor = register its target here; the API and UI pick it up
automatically (with a `configured` flag so dormant vendors show but disable).
"""

from __future__ import annotations

from modules.shopping.connectors.base import PurchaseConnector, ShoppingListTarget
from modules.shopping.connectors.exporters import CsvExportTarget, TextExportTarget
from modules.shopping.connectors.imap import ImapPurchaseConnector
from modules.shopping.connectors.kroger import KrogerCartTarget

_TARGETS: list[ShoppingListTarget] = [
    TextExportTarget(),
    CsvExportTarget(),
    KrogerCartTarget(),  # dormant until configured
]

# IN connectors — pull purchase history to seed intake. Dormant until configured.
_CONNECTORS: list[PurchaseConnector] = [
    ImapPurchaseConnector(),  # dormant until an IMAP mailbox is configured
]


def list_targets() -> list[ShoppingListTarget]:
    return list(_TARGETS)


def get_target(key: str) -> ShoppingListTarget | None:
    return next((t for t in _TARGETS if t.key == key), None)


def list_connectors() -> list[PurchaseConnector]:
    return list(_CONNECTORS)


def get_connector(key: str) -> PurchaseConnector | None:
    return next((c for c in _CONNECTORS if c.key == key), None)
