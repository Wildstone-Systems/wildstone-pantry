"""Outbound-webhook delivery log reads (ADR 0011 observability).

``webhook_events`` is a platform table written by ``handlers.webhook_handler``
(one row per outbound delivery attempt); this service is its read interface so
routes never query the model directly.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import WebhookEvent


def list_recent_deliveries(db: Session, *, limit: int = 10) -> list[WebhookEvent]:
    """Recent deliveries, newest first. Ordered by ``last_attempt_at`` — it
    carries microsecond precision, while ``created_at``'s server default is
    whole-second on SQLite, which makes same-second ordering unstable."""
    return list(
        db.execute(
            select(WebhookEvent)
            .order_by(WebhookEvent.last_attempt_at.desc(), WebhookEvent.created_at.desc())
            .limit(limit)
        ).scalars().all()
    )
