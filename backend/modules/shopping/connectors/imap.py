"""IMAP purchase connector (step 5b, IN).

Parses order-confirmation emails from the family's own mailbox over IMAP — no
Gmail API and no Google CASA review.
Dormant until ``IMAP_HOST``/``IMAP_USERNAME``/``IMAP_PASSWORD`` are configured;
``fetch_orders()`` is the only part that touches the network. The parse pipeline
it delegates to (``email_to_order``) is pure and fixture-tested.
"""

from __future__ import annotations

import email
import imaplib
from email.header import decode_header, make_header
from email.message import Message

from modules.shopping.connectors.base import (
    NormalizedOrder,
    NotConfiguredError,
)
from modules.shopping.connectors.email_order import email_to_order
from modules.shopping.services.email_receipt_fetcher import ALL_SENDERS


def _decode(value: str | None) -> str:
    """Best-effort decode of an RFC 2047 encoded header."""
    if not value:
        return ""
    try:
        return str(make_header(decode_header(value)))
    except Exception:
        return value


def _extract_bodies(msg: Message) -> tuple[str | None, str | None]:
    """Pull the HTML and plain-text bodies out of a parsed email message."""
    html_body: str | None = None
    text_body: str | None = None

    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_maintype() == "multipart":
                continue
            if part.get("Content-Disposition", "").lower().startswith("attachment"):
                continue
            ctype = part.get_content_type()
            if ctype not in ("text/html", "text/plain"):
                continue
            payload = part.get_payload(decode=True)
            if payload is None:
                continue
            charset = part.get_content_charset() or "utf-8"
            try:
                decoded = payload.decode(charset, errors="replace")
            except (LookupError, ValueError):
                decoded = payload.decode("utf-8", errors="replace")
            if ctype == "text/html" and html_body is None:
                html_body = decoded
            elif ctype == "text/plain" and text_body is None:
                text_body = decoded
    else:
        payload = msg.get_payload(decode=True)
        if payload is not None:
            charset = msg.get_content_charset() or "utf-8"
            try:
                decoded = payload.decode(charset, errors="replace")
            except (LookupError, ValueError):
                decoded = payload.decode("utf-8", errors="replace")
            if msg.get_content_type() == "text/html":
                html_body = decoded
            else:
                text_body = decoded

    return html_body, text_body


def message_to_order(raw: bytes) -> NormalizedOrder | None:
    """Parse one raw RFC822 message into a ``NormalizedOrder`` (or ``None``).

    Pure over its input — no network — so it is fixture-testable alongside
    ``email_to_order``.
    """
    msg = email.message_from_bytes(raw)
    sender = _decode(msg.get("From"))
    subject = _decode(msg.get("Subject"))
    message_id = (msg.get("Message-ID") or "").strip() or None
    html_body, text_body = _extract_bodies(msg)
    return email_to_order(
        sender=sender,
        subject=subject,
        html=html_body,
        text=text_body,
        message_id=message_id,
    )


class ImapPurchaseConnector:
    """IN connector: pull order-confirmation emails over IMAP and normalize them.

    Config keys: ``host``, ``port``, ``username``, ``password``, ``mailbox`` —
    resolved by the caller (DB override over env seed, ADR 0009) and passed in.
    """

    key = "imap"
    label = "Email receipts (IMAP)"

    def configured(self, config: dict[str, str]) -> bool:
        return bool(
            (config.get("host") or "").strip()
            and (config.get("username") or "").strip()
            and (config.get("password") or "").strip()
        )

    def verify(self, config: dict[str, str]) -> None:
        """Attempt a login (and mailbox select) to validate credentials.

        Raises ``NotConfiguredError`` when dormant; any imaplib error propagates
        so the admin sees the real failure reason.
        """
        if not self.configured(config):
            raise NotConfiguredError("IMAP mailbox is not configured")
        conn = imaplib.IMAP4_SSL(config["host"], int(config.get("port") or 993))
        try:
            conn.login(config["username"], config["password"])
            conn.select(config.get("mailbox") or "INBOX", readonly=True)
        finally:
            try:
                conn.logout()
            except Exception:
                pass

    def fetch_orders(self, config: dict[str, str], *, max_messages: int = 50) -> list[NormalizedOrder]:
        """Connect, search recent messages from known retailer senders, normalize.

        Raises ``NotConfiguredError`` when the mailbox isn't configured. This is
        the only method that opens a network connection.
        """
        if not self.configured(config):
            raise NotConfiguredError("IMAP mailbox is not configured")

        conn = imaplib.IMAP4_SSL(config["host"], int(config.get("port") or 993))
        try:
            conn.login(config["username"], config["password"])
            conn.select(config.get("mailbox") or "INBOX", readonly=True)

            # OR together the known retailer senders so we only pull receipts.
            criteria = _sender_search_criteria(ALL_SENDERS)
            typ, data = conn.search(None, criteria)
            if typ != "OK" or not data or not data[0]:
                return []

            ids = data[0].split()[-max_messages:]
            orders: list[NormalizedOrder] = []
            for msg_id in ids:
                typ, msg_data = conn.fetch(msg_id, "(RFC822)")
                if typ != "OK" or not msg_data:
                    continue
                raw = next((part[1] for part in msg_data if isinstance(part, tuple)), None)
                if not raw:
                    continue
                try:
                    order = message_to_order(raw)
                except Exception:
                    continue
                if order is not None:
                    orders.append(order)
            return orders
        finally:
            try:
                conn.logout()
            except Exception:
                pass


def _sender_search_criteria(senders: list[str]) -> str:
    """Build a nested-OR IMAP SEARCH string across sender addresses.

    IMAP's OR is binary, so a list folds into right-nested ORs:
    ``OR (FROM a) (OR (FROM b) (FROM c))``.
    """
    if not senders:
        return "ALL"
    if len(senders) == 1:
        return f'FROM "{senders[0]}"'
    head, *rest = senders
    return f'OR (FROM "{head}") ({_sender_search_criteria(rest)})'
