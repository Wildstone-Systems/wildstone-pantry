"""Vendor connector interfaces (step 5, ADR 0003 D2).

Connectors are pluggable and degrade gracefully:
- ``ShoppingListTarget`` (OUT): push the buy list to a destination (a plain
  text/CSV export, or a vendor cart such as Kroger). Credential-free targets are
  always available; vendor targets stay dormant until configured.
- ``PurchaseConnector`` (IN): pull a shopper's purchase history to seed intake
  (step 5b — IMAP email-receipt parsing). Defined here so the architecture is in
  place; no live IN connector ships in 5a.

Lots/expiry are never sourced from a vendor — the app owns those.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
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


# ---- IN (step 5b — defined now, implemented later) -------------------------


@dataclass(frozen=True)
class NormalizedLine:
    name: str
    quantity: Decimal
    unit: str
    upc: str | None = None
    unit_price: Decimal | None = None


@dataclass(frozen=True)
class NormalizedOrder:
    vendor: str
    purchased_on: date | None
    lines: list[NormalizedLine]
    # Stable per-source identifier (e.g. an email Message-ID) used to make intake
    # idempotent — re-importing the same order is skipped. None when the source
    # offers no stable id.
    external_id: str | None = None


@runtime_checkable
class PurchaseConnector(Protocol):
    """IN: pull normalized purchase history. Lots/expiry are NOT expected from
    the vendor — the app assigns those at intake.

    Config is passed in (resolved from the DB override over the env seed, ADR
    0009) rather than read from a global, so the connector stays a pure,
    testable unit and picks up in-app credential changes without a restart."""

    key: str
    label: str

    def configured(self, config: dict[str, str]) -> bool: ...

    def fetch_orders(self, config: dict[str, str]) -> list[NormalizedOrder]: ...
