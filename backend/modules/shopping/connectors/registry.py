"""Registry of shopping-list export targets (step 5).

Adding a vendor = register its target here; the API and UI pick it up
automatically (with a `configured` flag so dormant vendors show but disable).
"""

from __future__ import annotations

from modules.shopping.connectors.base import ShoppingListTarget
from modules.shopping.connectors.exporters import CsvExportTarget, TextExportTarget
from modules.shopping.connectors.kroger import KrogerCartTarget

_TARGETS: list[ShoppingListTarget] = [
    TextExportTarget(),
    CsvExportTarget(),
    KrogerCartTarget(),  # dormant until configured
]

def list_targets() -> list[ShoppingListTarget]:
    return list(_TARGETS)


def get_target(key: str) -> ShoppingListTarget | None:
    return next((t for t in _TARGETS if t.key == key), None)
