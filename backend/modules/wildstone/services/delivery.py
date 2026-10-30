"""Deliver queued envelopes to Keystone's household event intake (ADR 0026 §4).

``POST {keystone_url}/api/v1/household/events`` with the publisher token as a
bearer token. Keystone answers 201 for a new event and 200 for one it already
has, so a retry after a lost response is harmless.

Outcomes per row:

* 2xx → delivered.
* 400 / 413 / 422 → rejected: Keystone read it and refused it, and sending the
  same bytes again will not change its mind. Kept with the reason for the
  status screen.
* anything else (network, 401/403, 5xx) → wait and retry with backoff, and
  stop this batch: if Keystone is down or the token is wrong, the next row
  will fail the same way. Order is kept: nothing overtakes a waiting row.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

import httpx
from sqlalchemy.orm import Session

from modules.wildstone import repository
from modules.wildstone.services.connection import get_config, is_configured

logger = logging.getLogger(__name__)

INTAKE_PATH = "/api/v1/household/events"
BATCH = 50
TIMEOUT_SECONDS = 10.0
MAX_BACKOFF = timedelta(hours=1)
KEEP_DELIVERED = timedelta(days=30)
_PERMANENT = {400, 413, 422}

Poster = Callable[..., httpx.Response]


def _as_utc(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def _backoff(attempts: int) -> timedelta:
    return min(timedelta(seconds=5 * (2 ** max(attempts - 1, 0))), MAX_BACKOFF)


def post_envelope(url: str, token: str, envelope: dict, *, poster: Poster | None = None) -> httpx.Response:
    send = poster or httpx.post
    return send(
        f"{url}{INTAKE_PATH}",
        json=envelope,
        headers={"Authorization": f"Bearer {token}"},
        timeout=TIMEOUT_SECONDS,
    )


def deliver_pending(db: Session, *, now: datetime | None = None, poster: Poster | None = None) -> dict:
    """Send what is due. Returns counts for the caller's logs and tests."""
    counts = {"delivered": 0, "rejected": 0, "deferred": 0}
    if not is_configured(db):
        return counts
    now = now or datetime.now(UTC)
    config = get_config(db)

    rows = repository.pending_outbox(db, limit=BATCH)

    for row in rows:
        # Strict order: while the oldest event waits out its backoff, so does
        # everything behind it. Rules downstream see facts in the order they
        # happened, and a transient failure hits every row alike anyway.
        if row.next_attempt_at is not None and _as_utc(row.next_attempt_at) > now:
            break
        row.attempts += 1
        error: str | None = None
        try:
            resp = post_envelope(config["keystone_url"], config["publisher_token"], row.envelope, poster=poster)
        except httpx.HTTPError as exc:
            error = f"Wildstone is unreachable: {exc}"
        else:
            if 200 <= resp.status_code < 300:
                row.status, row.delivered_at, row.last_error = "delivered", now, None
                counts["delivered"] += 1
            elif resp.status_code in _PERMANENT:
                row.status, row.last_error = "rejected", f"{resp.status_code}: {resp.text[:500]}"
                counts["rejected"] += 1
                logger.warning("Wildstone rejected %s (%s): %s", row.event_type, row.event_id, row.last_error)
            elif resp.status_code in (401, 403):
                error = f"{resp.status_code}: Wildstone refused the publisher token — reconnect PantryOS"
            else:
                error = f"{resp.status_code}: {resp.text[:300]}"

        if error is not None:
            row.last_error = error
            row.next_attempt_at = now + _backoff(row.attempts)
            repository.save_outbox_row(db, row)
            db.commit()
            counts["deferred"] += 1
            break
        repository.save_outbox_row(db, row)
        db.commit()

    repository.prune_delivered(db, before=now - KEEP_DELIVERED)
    db.commit()
    return counts


def send_test(db: Session, *, poster: Poster | None = None) -> dict:
    """Prove the address and token work, right now, without queueing anything."""
    from modules.wildstone.domain.envelopes import connection_tested

    if not is_configured(db):
        raise ValueError("Wildstone is not connected")
    config = get_config(db)
    try:
        resp = post_envelope(
            config["keystone_url"], config["publisher_token"], connection_tested(now=datetime.now(UTC)), poster=poster
        )
    except httpx.HTTPError as exc:
        raise ConnectionError(f"Wildstone is unreachable at {config['keystone_url']}: {exc}") from exc
    if resp.status_code in (401, 403):
        raise PermissionError("Wildstone refused the publisher token")
    if not 200 <= resp.status_code < 300:
        raise ConnectionError(f"Wildstone answered {resp.status_code}: {resp.text[:300]}")
    return {"ok": True, "status": resp.status_code}
