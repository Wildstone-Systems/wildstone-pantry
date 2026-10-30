"""Email receipt parser — extract structured line items from HTML/text receipts.

Supports retailer-specific parsers (Kroger, Walmart, Target, etc.) and a generic
HTML-table-based fallback for unrecognized formats.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Protocol

from bs4 import BeautifulSoup

# Use the stdlib HTML parser so receipt parsing needs no extra native dependency
# (bs4 is declared; lxml is not, and would be a silent runtime requirement).
_HTML_PARSER = "html.parser"

# ---------------------------------------------------------------------------
# Parsed output types
# ---------------------------------------------------------------------------

@dataclass
class ParsedLineItem:
    description: str               # Raw text from receipt
    product_name: str | None       # Normalized/cleaned name
    quantity: Decimal = Decimal("1")
    unit: str = "each"
    price: Decimal | None = None
    category: str | None = None
    upc: str | None = None


@dataclass
class ParsedReceiptData:
    """Structured data extracted from a receipt email."""

    retailer: str | None
    store_name: str | None
    store_number: str | None
    purchase_date: date | None
    subtotal: Decimal | None = None
    tax: Decimal | None = None
    total: Decimal | None = None
    items: list[ParsedLineItem] = field(default_factory=list)
    raw_html: str | None = None
    raw_text: str | None = None
    parse_method: str = "generic"  # which parser was used


# ---------------------------------------------------------------------------
# Utility functions
# ---------------------------------------------------------------------------

_PRICE_RE = re.compile(r"\$\s?(\d+\.\d{2})")
_QUANTITY_RE = re.compile(r"(\d+(?:\.\d+)?)\s*x\s*", re.IGNORECASE)
_UPC_RE = re.compile(r"\b(\d{8,14})\b")
_DATE_RE = re.compile(r"(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})")


def _extract_price(text: str) -> Decimal | None:
    """Find the last dollar amount in a line of text."""
    matches = _PRICE_RE.findall(text)
    if matches:
        try:
            return Decimal(matches[-1])
        except Exception:
            pass
    return None


def _clean_item_name(raw: str) -> str:
    """Clean up a raw receipt line into a human-readable product name."""
    # Remove leading quantities like "3x"
    cleaned = _QUANTITY_RE.sub("", raw)
    # Remove trailing prices
    cleaned = _PRICE_RE.sub("", cleaned)
    # Remove UPCs
    cleaned = _UPC_RE.sub("", cleaned)
    # Remove extra whitespace
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    # Remove trailing punctuation
    cleaned = cleaned.rstrip(".,;:")
    # Title case
    if cleaned.isupper():
        cleaned = cleaned.title()
    return cleaned[:200] if cleaned else raw[:200]


def _parse_date(date_str: str | None) -> date | None:
    """Try to parse a date string in various formats."""
    if not date_str:
        return None
    formats = [
        "%m/%d/%Y", "%m/%d/%y", "%Y-%m-%d",
        "%m-%d-%Y", "%m-%d-%y",
        "%B %d, %Y", "%b %d, %Y",
        "%d %B %Y", "%d %b %Y",
    ]
    for fmt in formats:
        try:
            return datetime.strptime(date_str.strip(), fmt).date()
        except ValueError:
            continue
    return None


def _extract_decimal(pattern: re.Pattern, text: str) -> Decimal | None:
    """Find first match of a regex pattern and convert to Decimal."""
    match = pattern.search(text)
    if match:
        try:
            return Decimal(match.group(1).replace(",", ""))
        except Exception:
            pass
    return None


# ---------------------------------------------------------------------------
# Retailer-specific parsers
# ---------------------------------------------------------------------------

class ReceiptParser(Protocol):
    """Protocol for retailer-specific receipt parsers."""

    def can_parse(self, html: str, text: str | None, retailer: str | None) -> bool: ...
    def parse(self, html: str, text: str | None, retailer: str | None) -> ParsedReceiptData: ...


class KrogerParser:
    """Parser for Kroger digital receipt emails."""

    SUBTOTAL_RE = re.compile(r"subtotal[:\s]+\$?(\d+[\.,]?\d*)", re.IGNORECASE)
    TAX_RE = re.compile(r"tax[:\s]+\$?(\d+[\.,]?\d*)", re.IGNORECASE)
    TOTAL_RE = re.compile(r"total[:\s]+\$?(\d+[\.,]?\d*)", re.IGNORECASE)
    DATE_RE = re.compile(r"(\d{1,2}/\d{1,2}/\d{2,4})")
    STORE_RE = re.compile(r"store\s*#?\s*(\d+)", re.IGNORECASE)
    ITEM_LINE_RE = re.compile(r"^(.+?)\s+\$?(\d+\.\d{2})\s*$")

    def can_parse(self, html: str, text: str | None, retailer: str | None) -> bool:
        return retailer == "kroger" or "kroger" in html.lower()[:2000]

    def parse(self, html: str, text: str | None, retailer: str | None) -> ParsedReceiptData:
        soup = BeautifulSoup(html, _HTML_PARSER)
        body_text = soup.get_text(separator="\n")

        # Extract totals
        subtotal = _extract_decimal(self.SUBTOTAL_RE, body_text)
        tax = _extract_decimal(self.TAX_RE, body_text)
        total = _extract_decimal(self.TOTAL_RE, body_text)

        # Extract date
        date_match = self.DATE_RE.search(body_text[:500])
        purchase_date = _parse_date(date_match.group(1)) if date_match else None

        # Store number
        store_match = self.STORE_RE.search(body_text[:500])
        store_number = store_match.group(1) if store_match else None

        # Parse line items — look for lines with item name + price
        items: list[ParsedLineItem] = []
        lines = body_text.split("\n")
        for line in lines:
            line = line.strip()
            if not line or len(line) < 4:
                continue
            match = self.ITEM_LINE_RE.match(line)
            if match:
                raw_name = match.group(1).strip()
                price = Decimal(match.group(2))
                # Skip header/summary lines
                if any(kw in raw_name.lower() for kw in ["subtotal", "tax", "total", "savings", "change", "balance", "card"]):
                    continue
                items.append(ParsedLineItem(
                    description=raw_name,
                    product_name=_clean_item_name(raw_name),
                    price=price,
                ))

        return ParsedReceiptData(
            retailer="kroger",
            store_name="Kroger",
            store_number=store_number,
            purchase_date=purchase_date,
            subtotal=subtotal,
            tax=tax,
            total=total,
            items=items,
            raw_html=html,
            raw_text=text,
            parse_method="kroger",
        )


class WalmartParser:
    """Parser for Walmart receipt emails."""

    SUBTOTAL_RE = re.compile(r"sub\s*total[:\s]+\$?(\d+[\.,]?\d*)", re.IGNORECASE)
    TAX_RE = re.compile(r"(?:sales\s+)?tax[:\s]+\$?(\d+[\.,]?\d*)", re.IGNORECASE)
    TOTAL_RE = re.compile(r"(?:grand\s+)?total[:\s]+\$?(\d+[\.,]?\d*)", re.IGNORECASE)
    DATE_RE = re.compile(r"(\d{1,2}/\d{1,2}/\d{2,4})")
    STORE_RE = re.compile(r"store\s*#?\s*(\d+)", re.IGNORECASE)
    ITEM_LINE_RE = re.compile(r"^(.+?)\s+\$?(\d+\.\d{2})\s*$")

    def can_parse(self, html: str, text: str | None, retailer: str | None) -> bool:
        return retailer == "walmart" or "walmart" in html.lower()[:2000]

    def parse(self, html: str, text: str | None, retailer: str | None) -> ParsedReceiptData:
        soup = BeautifulSoup(html, _HTML_PARSER)
        body_text = soup.get_text(separator="\n")

        subtotal = _extract_decimal(self.SUBTOTAL_RE, body_text)
        tax = _extract_decimal(self.TAX_RE, body_text)
        total = _extract_decimal(self.TOTAL_RE, body_text)

        date_match = self.DATE_RE.search(body_text[:500])
        purchase_date = _parse_date(date_match.group(1)) if date_match else None

        store_match = self.STORE_RE.search(body_text[:500])
        store_number = store_match.group(1) if store_match else None

        items: list[ParsedLineItem] = []
        lines = body_text.split("\n")
        for line in lines:
            line = line.strip()
            if not line or len(line) < 4:
                continue
            match = self.ITEM_LINE_RE.match(line)
            if match:
                raw_name = match.group(1).strip()
                price = Decimal(match.group(2))
                if any(kw in raw_name.lower() for kw in ["subtotal", "tax", "total", "savings", "change", "payment", "cash"]):
                    continue
                items.append(ParsedLineItem(
                    description=raw_name,
                    product_name=_clean_item_name(raw_name),
                    price=price,
                ))

        return ParsedReceiptData(
            retailer="walmart",
            store_name="Walmart",
            store_number=store_number,
            purchase_date=purchase_date,
            subtotal=subtotal,
            tax=tax,
            total=total,
            items=items,
            raw_html=html,
            raw_text=text,
            parse_method="walmart",
        )


class TargetParser:
    """Parser for Target receipt emails."""

    SUBTOTAL_RE = re.compile(r"subtotal[:\s]+\$?(\d+[\.,]?\d*)", re.IGNORECASE)
    TAX_RE = re.compile(r"tax[:\s]+\$?(\d+[\.,]?\d*)", re.IGNORECASE)
    TOTAL_RE = re.compile(r"total[:\s]+\$?(\d+[\.,]?\d*)", re.IGNORECASE)
    DATE_RE = re.compile(r"(\d{1,2}/\d{1,2}/\d{2,4})")
    STORE_RE = re.compile(r"store\s*#?\s*(\d+)", re.IGNORECASE)
    ITEM_LINE_RE = re.compile(r"^(.+?)\s+\$?(\d+\.\d{2})\s*$")

    def can_parse(self, html: str, text: str | None, retailer: str | None) -> bool:
        return retailer == "target" or "target" in html.lower()[:2000]

    def parse(self, html: str, text: str | None, retailer: str | None) -> ParsedReceiptData:
        soup = BeautifulSoup(html, _HTML_PARSER)
        body_text = soup.get_text(separator="\n")

        subtotal = _extract_decimal(self.SUBTOTAL_RE, body_text)
        tax = _extract_decimal(self.TAX_RE, body_text)
        total = _extract_decimal(self.TOTAL_RE, body_text)

        date_match = self.DATE_RE.search(body_text[:500])
        purchase_date = _parse_date(date_match.group(1)) if date_match else None

        store_match = self.STORE_RE.search(body_text[:500])
        store_number = store_match.group(1) if store_match else None

        items: list[ParsedLineItem] = []
        lines = body_text.split("\n")
        for line in lines:
            line = line.strip()
            if not line or len(line) < 4:
                continue
            match = self.ITEM_LINE_RE.match(line)
            if match:
                raw_name = match.group(1).strip()
                price = Decimal(match.group(2))
                if any(kw in raw_name.lower() for kw in ["subtotal", "tax", "total", "savings", "redcard", "payment"]):
                    continue
                items.append(ParsedLineItem(
                    description=raw_name,
                    product_name=_clean_item_name(raw_name),
                    price=price,
                ))

        return ParsedReceiptData(
            retailer="target",
            store_name="Target",
            store_number=store_number,
            purchase_date=purchase_date,
            subtotal=subtotal,
            tax=tax,
            total=total,
            items=items,
            raw_html=html,
            raw_text=text,
            parse_method="target",
        )


# ---------------------------------------------------------------------------
# Generic HTML table parser (fallback)
# ---------------------------------------------------------------------------

class GenericParser:
    """Fallback parser that extracts items from HTML tables or structured text."""

    SUBTOTAL_RE = re.compile(r"sub\s*total[:\s]+\$?(\d+[\.,]?\d*)", re.IGNORECASE)
    TAX_RE = re.compile(r"(?:sales\s+)?tax[:\s]+\$?(\d+[\.,]?\d*)", re.IGNORECASE)
    TOTAL_RE = re.compile(r"(?:grand\s+)?total[:\s]+\$?(\d+[\.,]?\d*)", re.IGNORECASE)
    DATE_RE = re.compile(r"(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})")

    def can_parse(self, html: str, text: str | None, retailer: str | None) -> bool:
        return True  # Always works as fallback

    def parse(self, html: str, text: str | None, retailer: str | None) -> ParsedReceiptData:
        soup = BeautifulSoup(html, _HTML_PARSER)
        body_text = soup.get_text(separator="\n")

        subtotal = _extract_decimal(self.SUBTOTAL_RE, body_text)
        tax = _extract_decimal(self.TAX_RE, body_text)
        total = _extract_decimal(self.TOTAL_RE, body_text)

        date_match = self.DATE_RE.search(body_text[:500])
        purchase_date = _parse_date(date_match.group(1)) if date_match else None

        items: list[ParsedLineItem] = []

        # Strategy 1: Look for HTML tables with price columns
        tables = soup.find_all("table")
        for table in tables:
            rows = table.find_all("tr")
            for row in rows:
                cells = row.find_all(["td", "th"])
                if len(cells) < 2:
                    continue
                cell_texts = [c.get_text(strip=True) for c in cells]
                # Look for a row where one cell has text and another has a price
                name_cell = None
                price_cell = None
                for cell_text in cell_texts:
                    if _PRICE_RE.search(cell_text) and not price_cell:
                        price_cell = cell_text
                    elif len(cell_text) > 3 and not name_cell:
                        name_cell = cell_text

                if name_cell and price_cell:
                    price = _extract_price(price_cell)
                    if price and not any(kw in name_cell.lower() for kw in ["subtotal", "tax", "total", "savings", "payment", "change", "balance", "card"]):
                        items.append(ParsedLineItem(
                            description=name_cell,
                            product_name=_clean_item_name(name_cell),
                            price=price,
                        ))

        # Strategy 2: If table parsing found nothing, fall back to line-by-line text parsing
        if not items:
            for line in body_text.split("\n"):
                line = line.strip()
                if not line or len(line) < 4:
                    continue
                price = _extract_price(line)
                if price:
                    raw_name = _PRICE_RE.sub("", line).strip()
                    raw_name = re.sub(r"\s+", " ", raw_name).strip()
                    if len(raw_name) < 3:
                        continue
                    if any(kw in raw_name.lower() for kw in ["subtotal", "tax", "total", "savings", "payment", "change", "balance"]):
                        continue
                    items.append(ParsedLineItem(
                        description=line,
                        product_name=_clean_item_name(raw_name),
                        price=price,
                    ))

        return ParsedReceiptData(
            retailer=retailer,
            store_name=None,
            store_number=None,
            purchase_date=purchase_date,
            subtotal=subtotal,
            tax=tax,
            total=total,
            items=items,
            raw_html=html,
            raw_text=text,
            parse_method="generic",
        )


# ---------------------------------------------------------------------------
# Parser registry and dispatcher
# ---------------------------------------------------------------------------

_PARSERS: list[ReceiptParser] = [
    KrogerParser(),
    WalmartParser(),
    TargetParser(),
    GenericParser(),  # always last — matches everything
]


def parse_receipt_email(
    html: str | None,
    text: str | None = None,
    retailer: str | None = None,
) -> ParsedReceiptData:
    """Parse a receipt email using the best matching parser.

    Tries retailer-specific parsers first, falls back to generic.
    """
    content = html or text or ""
    if not content:
        return ParsedReceiptData(retailer=retailer, parse_method="empty")

    for parser in _PARSERS:
        if parser.can_parse(content, text, retailer):
            return parser.parse(content, text, retailer)

    # Should never reach here (GenericParser always matches)
    return ParsedReceiptData(retailer=retailer, parse_method="none")