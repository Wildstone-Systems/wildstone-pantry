"""Pure email → NormalizedOrder adapter (step 5b IN core).

This is the fixture-testable heart of the IMAP purchase connector: given the
raw pieces of an order-confirmation email (sender, subject, HTML/text bodies) it
produces a ``NormalizedOrder`` — or ``None`` when nothing parseable is found. It
reuses the existing retailer parsers (``parse_receipt_email``) and sender→retailer
detection (``_detect_retailer``); no mailbox or network is involved, so the whole
mapping can be exercised against saved fixture emails.

Lots/expiry are deliberately NOT sourced here — the app assigns those at intake
(ADR 0003 D2). A NormalizedOrder carries only what a receipt actually tells us:
what was bought, how much, and (when present) UPC and unit price.
"""

from __future__ import annotations

from decimal import Decimal

from modules.shopping.connectors.base import NormalizedLine, NormalizedOrder
from modules.shopping.services.email_receipt_fetcher import _detect_retailer
from modules.shopping.services.email_receipt_parser import (
    ParsedReceiptData,
    parse_receipt_email,
)


def parsed_to_order(
    parsed: ParsedReceiptData,
    *,
    fallback_vendor: str = "unknown",
    external_id: str | None = None,
) -> NormalizedOrder | None:
    """Map a ``ParsedReceiptData`` onto a ``NormalizedOrder``.

    Returns ``None`` when the parse yielded no usable line items — an empty order
    is not worth feeding into intake.
    """
    lines: list[NormalizedLine] = []
    for item in parsed.items:
        name = (item.product_name or item.description or "").strip()
        if not name:
            continue
        lines.append(
            NormalizedLine(
                name=name,
                quantity=item.quantity if item.quantity is not None else Decimal("1"),
                unit=item.unit or "each",
                upc=item.upc,
                unit_price=item.price,
            )
        )

    if not lines:
        return None

    return NormalizedOrder(
        vendor=parsed.retailer or fallback_vendor,
        purchased_on=parsed.purchase_date,
        lines=lines,
        external_id=external_id,
    )


def email_to_order(
    *,
    sender: str,
    subject: str,
    html: str | None,
    text: str | None,
    message_id: str | None = None,
) -> NormalizedOrder | None:
    """Turn one raw email into a ``NormalizedOrder``, or ``None`` if not parseable.

    ``sender`` and ``subject`` steer retailer selection; the bodies carry the
    line items. ``message_id`` (the email's Message-ID) becomes the order's stable
    ``external_id`` for idempotent intake. Detection falls back to the parsers' own
    content sniffing when the sender isn't a known retailer address.
    """
    retailer = _detect_retailer(sender)
    parsed = parse_receipt_email(html=html, text=text, retailer=retailer)
    return parsed_to_order(
        parsed,
        fallback_vendor=retailer or "unknown",
        external_id=(message_id or None),
    )
