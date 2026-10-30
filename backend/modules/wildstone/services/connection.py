"""The Wildstone connection: where Keystone is, and whether we're relaying (ADR 0026 §7).

Settings live in the ADR 0009 connector config (key ``wildstone``, encrypted).
Relay state lives in this module's own tables. Connecting starts the relay at
the ledger's current end — connecting never replays history — and
disconnecting drops anything not yet delivered, so reconnecting months later
does not send stale news.
"""

from __future__ import annotations

from datetime import UTC, datetime
from urllib.parse import urlparse

from sqlalchemy.orm import Session

from app.services.app_settings import get_setting, is_true, set_setting
from modules.inventory.services.feed import head_seq
from modules.shopping.services import connector_config
from modules.shopping.services.shopping_list import items_below_threshold, list_state
from modules.wildstone import repository
from modules.wildstone.models import WildstoneRelayState, WildstoneStockFlag
from modules.wildstone.services import web_url

CONNECTOR_KEY = "wildstone"
STATE_NAME = "default"
# Set by Disconnect, cleared by connecting (or pairing) again. A managed
# install brings its address and token in the environment, where clearing the
# saved settings cannot reach; this is what makes Disconnect stick anyway.
DISCONNECTED_SETTING = "wildstone_disconnected"


def _now() -> datetime:
    return datetime.now(UTC)


def get_config(db: Session) -> dict[str, str]:
    config = connector_config.get_effective(db, CONNECTOR_KEY)
    return {
        "keystone_url": (config.get("keystone_url") or "").strip().rstrip("/"),
        "publisher_token": (config.get("publisher_token") or "").strip(),
    }


def is_configured(db: Session) -> bool:
    if is_true(db, DISCONNECTED_SETTING):
        return False
    config = get_config(db)
    return bool(config["keystone_url"] and config["publisher_token"])


def get_state(db: Session, *, for_update: bool = False) -> WildstoneRelayState | None:
    return repository.get_relay_state(db, STATE_NAME, for_update=for_update)


def validate_url(url: str) -> str:
    cleaned = (url or "").strip().rstrip("/")
    parsed = urlparse(cleaned)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ValueError("keystone_url must be an http(s) address, e.g. https://wildstone.home.arpa")
    return cleaned


def _start_relay(db: Session) -> None:
    """Begin at "now": the ledger's end, the current clock, today's low stock."""
    start = head_seq(db)
    repository.add_relay_state(
        db,
        WildstoneRelayState(
            name=STATE_NAME,
            ledger_seq=start,
            start_seq=start,
            cook_cursor=_now(),
            # The list as it stands is the starting picture, not news.
            list_fingerprint=list_state(db)["fingerprint"],
            updated_at=_now(),
        ),
    )
    # Items already low are the starting picture, not news.
    for item in items_below_threshold(db):
        repository.merge_stock_flag(
            db, WildstoneStockFlag(item_id=item["item_id"], low=True, crossings=0, updated_at=_now())
        )


def ensure_started(db: Session) -> None:
    """Start the relay for a connection that arrived by environment (the
    managed shape) rather than through :func:`configure`."""
    if is_configured(db) and get_state(db) is None:
        _start_relay(db)
        db.commit()


def _reset_relay(db: Session, *, forget_history: bool = False) -> None:
    """Forget everything relayed so far: undelivered events, stock picture,
    cursors. Delivered rows stay as history for the status screen, unless the
    connection now points at a different Keystone."""
    repository.reset_relay(db, forget_history=forget_history)


def configure(db: Session, *, keystone_url: str, publisher_token: str | None) -> None:
    """Connect, or change the connection.

    Pointing at a DIFFERENT Keystone is a new connection: events queued for the
    old household must not be sent to the new one, and the new one starts at
    "now" like any first connection. Changing only the token keeps the queue.
    """
    previous = get_config(db)["keystone_url"]
    updates = {"keystone_url": validate_url(keystone_url)}
    new_target = bool(previous) and previous != updates["keystone_url"]
    if publisher_token and publisher_token.strip():
        updates["publisher_token"] = publisher_token.strip()
    elif not get_config(db)["publisher_token"]:
        raise ValueError("A publisher token is required the first time you connect")
    elif new_target:
        # A token is minted by one Keystone; the old one would only be refused.
        raise ValueError("A different Wildstone needs its own publisher token")
    try:
        if new_target:
            # Uncommitted: set_config commits it together with the new target,
            # so a save that fails (e.g. no encryption key) keeps the old queue.
            _reset_relay(db, forget_history=True)
        connector_config.set_config(db, CONNECTOR_KEY, updates)
    except Exception:
        db.rollback()
        raise
    set_setting(db, DISCONNECTED_SETTING, "false")
    ensure_started(db)


def disconnect(db: Session) -> None:
    """Stop relaying until someone connects again — including a connection
    that came from the environment, which stays set but no longer counts."""
    connector_config.clear_config(db, CONNECTOR_KEY)
    _reset_relay(db)
    set_setting(db, DISCONNECTED_SETTING, "true")  # commits


def status(db: Session) -> dict:
    config = get_config(db)
    state = get_state(db)
    summary = repository.outbox_summary(db)
    counts = summary["counts"]
    last_delivered = summary["last_delivered_at"]
    last_error = summary["last_error"]
    return {
        "connected": is_configured(db),
        # When a pairing last completed (None if never, or connected by hand).
        "paired_at": get_setting(db, "wildstone_paired_at"),
        "keystone_url": config["keystone_url"],
        "publisher_token_set": bool(config["publisher_token"]),
        # What pairing tells Wildstone about where people open PantryOS.
        **web_url.status(),
        "relay": {
            "ledger_seq": state.ledger_seq if state else None,
            "pending": counts.get("pending", 0),
            "delivered": counts.get("delivered", 0),
            "rejected": counts.get("rejected", 0),
            "last_delivered_at": last_delivered,
            "last_error": last_error,
        },
    }
