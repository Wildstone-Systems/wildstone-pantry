"""Vendor connector interfaces (step 5, ADR 0003 D2).

Connectors are pluggable and degrade gracefully:
- ``ShoppingListTarget`` (OUT): push the buy list to a destination (a plain
  text/CSV export, or a vendor cart such as Kroger). Credential-free targets are
  always available; vendor targets stay dormant until configured.

Lots/expiry are never sourced from a vendor — the app owns those.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol, runtime_checkable


class NotConfiguredError(Exception):
    """A vendor connector was invoked but its credentials aren't configured."""


@dataclass(frozen=True)
class ExportLine:
    name: str
    quantity: Decimal
    unit: str


@dataclass(frozen=True)
class ExportResult:
    target: str  # target key
    format: str  # "text" | "csv" | "url"
    content_type: str  # e.g. "text/plain", "text/csv"
    body: str  # the export content (text/csv) or a URL
    filename: str | None = None  # suggested download filename


@runtime_checkable
class ShoppingListTarget(Protocol):
    """OUT: turn the pending buy list into something the user can act on."""

    key: str
    label: str

    def configured(self) -> bool:
        """False for a dormant vendor (missing credentials) — the UI shows it but disables it."""
        ...

    def export(self, lines: list[ExportLine]) -> ExportResult:
        """Build the export. Raise ``NotConfiguredError`` if not configured."""
        ...
