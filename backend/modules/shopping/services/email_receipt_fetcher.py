"""Gmail receipt email fetcher.

Authenticates with Google OAuth, queries Gmail for receipt emails
from known retailers, and returns raw HTML/text payloads for parsing.
"""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from pathlib import Path

# NOTE: Google client libraries (`google-auth`, `google-api-python-client`) are an
# optional integration. They are imported lazily inside the functions that use them
# so the rest of the app (and the test harness) can import this module without them.
# Type annotations below stay valid as strings via `from __future__ import annotations`.
if False:  # typing-only; never executed
    from google.oauth2.credentials import Credentials

# ---------------------------------------------------------------------------
# Retailer sender addresses used for Gmail query filtering
# ---------------------------------------------------------------------------

RETAILER_SENDERS: dict[str, list[str]] = {
    "kroger": [
        "receipts@kroger.com",
        "no-reply@kroger.com",
    ],
    "walmart": [
        "order@walmart.com",
        "receipts@walmart.com",
    ],
    "target": [
        "targetnews@target.com",
        "no-reply@target.com",
    ],
    "costco": [
        "online@costco.com",
        "no-reply@costco.com",
    ],
    "amazon": [
        "shipment-confirmation@amazon.com",
        "auto-confirm@amazon.com",
        "order-confirm@amazon.com",
    ],
    "instacart": [
        "receipts@instacart.com",
    ],
    "sams_club": [
        "no-reply@samsclub.com",
    ],
    "whole_foods": [
        "no-reply@wholefoods.com",
    ],
    "trader_joes": [
        "no-reply@traderjoes.com",
    ],
    "aldi": [
        "no-reply@aldi.com",
    ],
}

# All unique sender addresses for "any retailer" queries
ALL_SENDERS = sorted({s for senders in RETAILER_SENDERS.values() for s in senders})


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class RawReceiptEmail:
    """A receipt email fetched from Gmail, not yet parsed."""

    message_id: str
    thread_id: str
    retailer: str | None          # matched retailer key, e.g. "kroger"
    sender: str                   # raw From header
    subject: str
    date: datetime | None        # parsed Date header
    html_body: str | None         # decoded HTML content
    text_body: str | None         # decoded plain text content
    snippet: str                  # Gmail-provided snippet (first ~200 chars)


@dataclass
class FetchResult:
    """Result of a receipt email fetch operation."""

    emails: list[RawReceiptEmail] = field(default_factory=list)
    total_fetched: int = 0
    errors: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Gmail query builder
# ---------------------------------------------------------------------------

def _build_gmail_query(
    retailer: str | None = None,
    after: str | None = None,
    before: str | None = None,
    max_age_days: int | None = None,
) -> str:
    """Build a Gmail search query string for receipt emails.

    Args:
        retailer: Specific retailer key (e.g. "kroger") or None for all.
        after: ISO date string (YYYY-MM-DD) for after: filter.
        before: ISO date string (YYYY-MM-DD) for before: filter.
        max_age_days: If set, calculate 'after' from today minus this many days.
    """
    parts: list[str] = []

    # Sender filter
    if retailer and retailer in RETAILER_SENDERS:
        sender_parts = [f"from:{addr}" for addr in RETAILER_SENDERS[retailer]]
        parts.append(f"({' OR '.join(sender_parts)})")
    else:
        sender_parts = [f"from:{addr}" for addr in ALL_SENDERS]
        parts.append(f"({' OR '.join(sender_parts)})")

    # Category/receipt keywords to narrow results
    parts.append('(subject:receipt OR subject:order OR subject:confirmation OR subject:purchase)')

    # Date filters
    if max_age_days is not None and after is None:
        from datetime import timedelta
        after = (datetime.now(UTC) - timedelta(days=max_age_days)).strftime("%Y-%m-%d")

    if after:
        parts.append(f"after:{after}")
    if before:
        parts.append(f"before:{before}")

    return " ".join(parts)


# ---------------------------------------------------------------------------
# Retailer detection from sender
# ---------------------------------------------------------------------------

def _detect_retailer(sender: str) -> str | None:
    """Match a sender address to a retailer key."""
    sender_lower = sender.lower()
    for retailer_key, addresses in RETAILER_SENDERS.items():
        for addr in addresses:
            if addr.lower() in sender_lower:
                return retailer_key
    return None


# ---------------------------------------------------------------------------
# Email body extraction
# ---------------------------------------------------------------------------

def _extract_body_from_payload(payload: dict) -> tuple[str | None, str | None]:
    """Extract HTML and plain text body from a Gmail message payload.

    Returns (html_body, text_body). Either may be None.
    """
    html_body = None
    text_body = None

    parts = payload.get("parts", [])
    if not parts:
        # Single-part message
        mime_type = payload.get("mimeType", "")
        data = payload.get("body", {}).get("data", "")
        if data:
            decoded = base64.urlsafe_b64decode(data).decode("utf-8", errors="replace")
            if "html" in mime_type:
                html_body = decoded
            else:
                text_body = decoded
        return html_body, text_body

    # Multipart: walk all parts recursively
    stack = list(parts)
    while stack:
        part = stack.pop(0)
        mime_type = part.get("mimeType", "")
        data = part.get("body", {}).get("data", "")

        if data:
            try:
                decoded = base64.urlsafe_b64decode(data).decode("utf-8", errors="replace")
            except Exception:
                continue
            if "html" in mime_type:
                html_body = decoded
            elif mime_type.startswith("text/"):
                text_body = decoded

        sub_parts = part.get("parts", [])
        if sub_parts:
            stack.extend(sub_parts)

    return html_body, text_body


def _extract_header(headers: list[dict], name: str) -> str | None:
    """Get a header value by name (case-insensitive)."""
    name_lower = name.lower()
    for h in headers:
        if h.get("name", "").lower() == name_lower:
            return h.get("value")
    return None


# ---------------------------------------------------------------------------
# Token management
# ---------------------------------------------------------------------------

class TokenNotFoundError(FileNotFoundError):
    pass


def _load_credentials(token_path: str | Path | None = None) -> Credentials:
    """Load and refresh Google OAuth credentials from the local token file.

    Args:
        token_path: Path to token.json. Defaults to ~/.config/pantryos-gmail/token.json

    Returns:
        Valid Credentials object with refreshed access token.

    Raises:
        TokenNotFoundError: If the token file doesn't exist.
    """
    if token_path is None:
        token_path = Path.home() / ".config" / "pantryos-gmail" / "token.json"
    token_path = Path(token_path)

    if not token_path.exists():
        raise TokenNotFoundError(f"Google OAuth token not found at {token_path}")

    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials

    token_data = json.loads(token_path.read_text())
    creds = Credentials.from_authorized_user_info(token_data)

    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
        # Persist the refreshed token
        token_path.write_text(json.dumps({
            "token": creds.token,
            "refresh_token": creds.refresh_token,
            "token_uri": creds.token_uri,
            "client_id": creds.client_id,
            "client_secret": creds.client_secret,
            "scopes": list(creds.scopes) if creds.scopes else [],
            "expiry": creds.expiry.isoformat() if creds.expiry else None,
            "universe_domain": "googleapis.com",
            "account": "",
        }, indent=2))

    return creds


# ---------------------------------------------------------------------------
# Main fetcher class
# ---------------------------------------------------------------------------

class GmailReceiptFetcher:
    """Fetch receipt emails from Gmail via the Google API."""

    def __init__(self, token_path: str | Path | None = None):
        self._token_path = token_path
        self._service = None

    def _get_service(self):
        """Lazy-initialize the Gmail API service."""
        if self._service is None:
            from googleapiclient.discovery import build

            creds = _load_credentials(self._token_path)
            self._service = build("gmail", "v1", credentials=creds)
        return self._service

    def list_receipt_emails(
        self,
        *,
        retailer: str | None = None,
        after: str | None = None,
        before: str | None = None,
        max_age_days: int | None = 30,
        max_results: int = 50,
        page_token: str | None = None,
    ) -> dict:
        """List receipt email message IDs matching the query.

        Returns dict with keys: messages (list of {id, threadId}), nextPageToken, resultSizeEstimate.
        """
        service = self._get_service()
        query = _build_gmail_query(
            retailer=retailer,
            after=after,
            before=before,
            max_age_days=max_age_days,
        )

        result = service.users().messages().list(
            userId="me",
            q=query,
            maxResults=max_results,
            pageToken=page_token,
        ).execute()

        return result

    def fetch_receipt_email(self, message_id: str) -> RawReceiptEmail | None:
        """Fetch and decode a single receipt email by message ID.

        Returns a RawReceiptEmail or None if the message couldn't be decoded.
        """
        service = self._get_service()

        try:
            msg = service.users().messages().get(
                userId="me",
                id=message_id,
                format="full",
            ).execute()
        except Exception as exc:
            raise RuntimeError(f"Failed to fetch message {message_id}: {exc}") from exc

        headers = msg.get("payload", {}).get("headers", [])
        sender = _extract_header(headers, "From") or ""
        subject = _extract_header(headers, "Subject") or ""
        date_str = _extract_header(headers, "Date")

        date_parsed = None
        if date_str:
            try:
                date_parsed = parsedate_to_datetime(date_str)
            except Exception:
                pass

        snippet = msg.get("snippet", "")
        html_body, text_body = _extract_body_from_payload(msg.get("payload", {}))

        return RawReceiptEmail(
            message_id=message_id,
            thread_id=msg.get("threadId", ""),
            retailer=_detect_retailer(sender),
            sender=sender,
            subject=subject,
            date=date_parsed,
            html_body=html_body,
            text_body=text_body,
            snippet=snippet,
        )

    def fetch_receipts(
        self,
        *,
        retailer: str | None = None,
        after: str | None = None,
        before: str | None = None,
        max_age_days: int | None = 30,
        max_results: int = 20,
    ) -> FetchResult:
        """Fetch multiple receipt emails and return parsed RawReceiptEmail objects.

        This is the main entry point: list matching emails → fetch each → return results.
        """
        result = FetchResult()

        try:
            list_result = self.list_receipt_emails(
                retailer=retailer,
                after=after,
                before=before,
                max_age_days=max_age_days,
                max_results=max_results,
            )
        except Exception as exc:
            result.errors.append(f"Gmail list failed: {exc}")
            return result

        messages = list_result.get("messages", [])
        result.total_fetched = len(messages)

        for msg_summary in messages:
            msg_id = msg_summary.get("id")
            if not msg_id:
                continue
            try:
                raw_email = self.fetch_receipt_email(msg_id)
                if raw_email:
                    result.emails.append(raw_email)
            except Exception as exc:
                result.errors.append(f"Message {msg_id}: {exc}")

        return result

    def discover_retailers(self, max_age_days: int = 90) -> list[dict]:
        """Scan recent email for receipt-sending addresses and return which retailers are present.

        Returns list of dicts: [{retailer, sender, count, latest_date}].
        """
        service = self._get_service()
        results: list[dict] = []

        for retailer_key, sender_addresses in RETAILER_SENDERS.items():
            # Query for each retailer separately to get counts
            sender_query = " OR ".join(f"from:{addr}" for addr in sender_addresses)
            query = f"({sender_query}) subject:(receipt OR order OR confirmation OR purchase)"

            if max_age_days:
                from datetime import timedelta
                after = (datetime.now(UTC) - timedelta(days=max_age_days)).strftime("%Y-%m-%d")
                query += f" after:{after}"

            try:
                list_result = service.users().messages().list(
                    userId="me",
                    q=query,
                    maxResults=1,  # Just need count info
                ).execute()

                messages = list_result.get("messages", [])
                estimate = list_result.get("resultSizeEstimate", 0)

                latest_date = None
                if messages:
                    # Fetch one to get the date
                    msg = service.users().messages().get(
                        userId="me",
                        id=messages[0]["id"],
                        format="metadata",
                        metadataHeaders=["Date"],
                    ).execute()
                    headers = msg.get("payload", {}).get("headers", [])
                    date_str = _extract_header(headers, "Date")
                    if date_str:
                        try:
                            latest_date = parsedate_to_datetime(date_str).isoformat()
                        except Exception:
                            pass

                if estimate > 0 or messages:
                    results.append({
                        "retailer": retailer_key,
                        "senders": sender_addresses,
                        "estimated_count": estimate,
                        "latest_date": latest_date,
                    })
            except Exception:
                continue

        return results