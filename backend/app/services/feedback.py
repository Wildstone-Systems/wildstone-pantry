from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Feedback


def create_feedback(
    db: Session,
    *,
    user_id: UUID,
    category: str,
    route: str | None,
    message: str,
) -> Feedback:
    row = Feedback(
        user_id=user_id,
        category=(category or "bug")[:24],
        route=(route[:256] if route else None),
        message=message,
        status="open",
    )
    db.add(row)
    db.commit()
    db.refresh(row)

    return row


def list_feedback(db: Session, *, user_id: UUID | None, status: str | None = None, limit: int = 50) -> list[Feedback]:
    capped = max(1, min(int(limit), 200))
    q = select(Feedback).order_by(Feedback.created_at.desc()).limit(capped)
    if user_id is not None:
        q = q.where(Feedback.user_id == user_id)
    # Exact match filter (e.g. "open", "resolved") to support QA triage workflows.
    normalized_status = (status or "").strip()
    if normalized_status:
        q = q.where(Feedback.status == normalized_status[:24])
    return list(db.execute(q).scalars().all())


def update_feedback_status(db: Session, *, feedback_id: UUID, status: str) -> Feedback:
    row = db.execute(select(Feedback).where(Feedback.id == feedback_id)).scalar_one_or_none()
    if not row:
        raise LookupError("Feedback not found")
    row.status = (status or "open")[:24]
    db.add(row)
    db.commit()
    db.refresh(row)
    return row
