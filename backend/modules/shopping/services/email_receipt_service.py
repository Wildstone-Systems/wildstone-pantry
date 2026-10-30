"""Email receipt service — orchestrates fetching, parsing, and storing receipt emails.

Integrates GmailReceiptFetcher + EmailReceiptParser with PantryOS's
existing Receipt / ReceiptLineItem model and inventory system.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal
from uuid import UUID

from sqlalchemy.orm import Session

from modules.automation import ExpirationEstimator
from app.models import Item, Receipt, ReceiptLineItem
from modules.catalog.services.barcodes import find_item_by_name
from modules.inventory.services.inventory import add_inventory, get_location
from modules.shopping.services.email_receipt_fetcher import (
    GmailReceiptFetcher,
    TokenNotFoundError,
)
from modules.shopping.services.email_receipt_parser import (
    ParsedReceiptData,
    parse_receipt_email,
)

# ---------------------------------------------------------------------------
# Response types
# ---------------------------------------------------------------------------

@dataclass
class DiscoveredRetailer:
    retailer: str
    senders: list[str]
    estimated_count: int
    latest_date: str | None


@dataclass
class DiscoverResult:
    retailers: list[DiscoveredRetailer] = field(default_factory=list)
    error: str | None = None


@dataclass
class FetchedReceiptSummary:
    """Summary of a fetched (but not yet parsed) receipt email."""

    message_id: str
    retailer: str | None
    sender: str
    subject: str
    date: str | None
    snippet: str


@dataclass
class FetchListResult:
    emails: list[FetchedReceiptSummary] = field(default_factory=list)
    total_available: int = 0
    error: str | None = None


@dataclass
class ParsedReceiptPreview:
    """Preview of a parsed receipt email before committing to inventory."""

    message_id: str
    retailer: str | None
    store_name: str | None
    store_number: str | None
    purchase_date: str | None
    subtotal: str | None
    tax: str | None
    total: str | None
    items: list[dict] = field(default_factory=list)
    item_count: int = 0
    parse_method: str = "generic"


@dataclass
class PreviewBatchResult:
    previews: list[ParsedReceiptPreview] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


@dataclass
class ImportResult:
    receipt_id: str | None = None
    status: str = "error"
    item_count: int = 0
    inventory_transactions: list[str] = field(default_factory=list)
    error: str | None = None


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------

class EmailReceiptService:
    """High-level service for email receipt discovery, preview, and import."""

    def __init__(self, token_path: str | None = None):
        self._fetcher = GmailReceiptFetcher(token_path=token_path)

    # ---- Discover ----

    def discover_retailers(self, max_age_days: int = 90) -> DiscoverResult:
        """Scan Gmail for receipt-sending retailers and return what's available."""
        try:
            raw = self._fetcher.discover_retailers(max_age_days=max_age_days)
        except TokenNotFoundError as exc:
            return DiscoverResult(error=f"Google OAuth not configured: {exc}")
        except Exception as exc:
            return DiscoverResult(error=f"Gmail discovery failed: {exc}")

        result = DiscoverResult()
        for r in raw:
            result.retailers.append(DiscoveredRetailer(
                retailer=r["retailer"],
                senders=r["senders"],
                estimated_count=r.get("estimated_count", 0),
                latest_date=r.get("latest_date"),
            ))
        return result

    # ---- List ----

    def list_receipts(
        self,
        *,
        retailer: str | None = None,
        max_age_days: int = 30,
        max_results: int = 20,
    ) -> FetchListResult:
        """List receipt emails matching the query without fetching full content."""
        try:
            list_result = self._fetcher.list_receipt_emails(
                retailer=retailer,
                max_age_days=max_age_days,
                max_results=max_results,
            )
        except TokenNotFoundError as exc:
            return FetchListResult(error=f"Google OAuth not configured: {exc}")
        except Exception as exc:
            return FetchListResult(error=f"Gmail list failed: {exc}")

        result = FetchListResult()
        messages = list_result.get("messages", [])
        result.total_available = list_result.get("resultSizeEstimate", len(messages))

        # We need to fetch metadata (sender, subject, date) for each message
        for msg_summary in messages[:max_results]:
            msg_id = msg_summary.get("id")
            if not msg_id:
                continue
            try:
                raw = self._fetcher.fetch_receipt_email(msg_id)
                if raw:
                    result.emails.append(FetchedReceiptSummary(
                        message_id=raw.message_id,
                        retailer=raw.retailer,
                        sender=raw.sender,
                        subject=raw.subject,
                        date=raw.date.isoformat() if raw.date else None,
                        snippet=raw.snippet,
                    ))
            except Exception:
                continue

        return result

    # ---- Preview ----

    def preview_receipt(self, message_id: str) -> ParsedReceiptPreview | None:
        """Fetch and parse a single receipt email, returning a preview for user review."""
        try:
            raw = self._fetcher.fetch_receipt_email(message_id)
        except Exception:
            return None

        if not raw:
            return None

        parsed = parse_receipt_email(
            html=raw.html_body,
            text=raw.text_body,
            retailer=raw.retailer,
        )

        return _parsed_to_preview(raw.message_id, parsed)

    def preview_receipts(
        self,
        *,
        retailer: str | None = None,
        max_age_days: int = 30,
        max_results: int = 10,
    ) -> PreviewBatchResult:
        """Fetch and parse multiple receipt emails for batch preview."""
        result = PreviewBatchResult()

        try:
            fetch_result = self._fetcher.fetch_receipts(
                retailer=retailer,
                max_age_days=max_age_days,
                max_results=max_results,
            )
        except TokenNotFoundError as exc:
            result.errors.append(f"Google OAuth not configured: {exc}")
            return result
        except Exception as exc:
            result.errors.append(f"Gmail fetch failed: {exc}")
            return result

        for raw in fetch_result.emails:
            try:
                parsed = parse_receipt_email(
                    html=raw.html_body,
                    text=raw.text_body,
                    retailer=raw.retailer,
                )
                result.previews.append(_parsed_to_preview(raw.message_id, parsed))
            except Exception as exc:
                result.errors.append(f"Message {raw.message_id}: {exc}")

        result.errors.extend(fetch_result.errors)
        return result

    # ---- Import ----

    def import_receipt(
        self,
        db: Session,
        *,
        message_id: str,
        location_id: UUID,
    ) -> ImportResult:
        """Fetch, parse, and import a single receipt email into inventory.

        This creates a Receipt record, matches/creates Items, and adds to inventory.
        """
        # Fetch and parse
        try:
            raw = self._fetcher.fetch_receipt_email(message_id)
        except Exception as exc:
            return ImportResult(error=f"Fetch failed: {exc}")

        if not raw:
            return ImportResult(error="Email not found")

        parsed = parse_receipt_email(
            html=raw.html_body,
            text=raw.text_body,
            retailer=raw.retailer,
        )

        if not parsed.items:
            return ImportResult(error="No line items found in receipt")

        # Validate location
        location = get_location(db, location_id)
        if not location:
            return ImportResult(error="Location not found")

        # Create Receipt record
        receipt = Receipt(
            source_filename=f"email:{message_id}",
            raw_text=raw.text_body or raw.html_body or "",
            status="processing",
        )
        db.add(receipt)
        db.commit()
        db.refresh(receipt)

        estimator = ExpirationEstimator()
        tx_ids: list[str] = []
        imported_count = 0

        for line_item in parsed.items:
            item_name = line_item.product_name or line_item.description
            if not item_name or len(item_name) < 2:
                continue

            # Try to match existing item
            item = find_item_by_name(db, item_name)
            if not item:
                estimated_days = estimator.estimate(item_name, None)
                item = Item(name=item_name, default_expiration_days=estimated_days)
                db.add(item)
                db.commit()
                db.refresh(item)
            else:
                estimated_days = item.default_expiration_days or estimator.estimate(item.name, None)

            expiration_date = date.today() + timedelta(days=int(estimated_days))

            qty = line_item.quantity if line_item.quantity > 0 else Decimal("1")
            unit = line_item.unit or "each"

            try:
                tx, _row, _item = add_inventory(
                    db,
                    item_id=item.id,
                    location_id=location_id,
                    quantity=qty,
                    unit=unit,
                    expiration_date=expiration_date,
                    reason="email_receipt_import",
                    note=f"receipt:{receipt.id}:email:{message_id}",
                )
                tx_ids.append(str(tx.id))
            except Exception:
                continue

            # Create ReceiptLineItem
            rli = ReceiptLineItem(
                receipt_id=receipt.id,
                raw_line=line_item.description,
                parsed_name=item_name,
                parsed_quantity=qty,
                parsed_unit=unit,
                matched_item_id=item.id,
                expiration_days_estimate=int(estimated_days),
            )
            db.add(rli)
            db.commit()
            imported_count += 1

        receipt.status = "processed"
        db.add(receipt)
        db.commit()

        return ImportResult(
            receipt_id=str(receipt.id),
            status="processed",
            item_count=imported_count,
            inventory_transactions=tx_ids,
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parsed_to_preview(message_id: str, parsed: ParsedReceiptData) -> ParsedReceiptPreview:
    """Convert a ParsedReceiptData into a ParsedReceiptPreview dict."""
    items_out: list[dict] = []
    for item in parsed.items:
        items_out.append({
            "description": item.description,
            "product_name": item.product_name,
            "quantity": str(item.quantity),
            "unit": item.unit,
            "price": str(item.price) if item.price is not None else None,
            "category": item.category,
        })

    return ParsedReceiptPreview(
        message_id=message_id,
        retailer=parsed.retailer,
        store_name=parsed.store_name,
        store_number=parsed.store_number,
        purchase_date=parsed.purchase_date.isoformat() if parsed.purchase_date else None,
        subtotal=str(parsed.subtotal) if parsed.subtotal is not None else None,
        tax=str(parsed.tax) if parsed.tax is not None else None,
        total=str(parsed.total) if parsed.total is not None else None,
        items=items_out,
        item_count=len(items_out),
        parse_method=parsed.parse_method,
    )