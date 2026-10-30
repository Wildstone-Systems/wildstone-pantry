"""Only the automation-owned result table is accessed here."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import delete, select, text
from sqlalchemy.orm import Session

from modules.automation.models import ClassifierEvaluation

MAX_RESULTS = 100


def save_result(db: Session, result: dict, now: datetime, expires: datetime) -> None:
    # SQLite's first DELETE acquires its write lock; PostgreSQL needs an explicit
    # short table lock so concurrent workers cannot each observe 99 rows and
    # jointly exceed the cap. No provider call occurs inside this transaction.
    if db.bind.dialect.name == "postgresql":
        db.execute(text("LOCK TABLE classifier_evaluations IN EXCLUSIVE MODE"))
    db.execute(delete(ClassifierEvaluation).where(ClassifierEvaluation.expires_at <= now))
    db.add(ClassifierEvaluation(id=UUID(result["id"]), created_at=now, expires_at=expires, result=result))
    db.flush()
    excess = db.scalars(
        select(ClassifierEvaluation.id)
        .order_by(ClassifierEvaluation.created_at.desc(), ClassifierEvaluation.id.desc())
        .offset(MAX_RESULTS)
    ).all()
    if excess:
        db.execute(delete(ClassifierEvaluation).where(ClassifierEvaluation.id.in_(excess)))
    db.commit()


def read_result(db: Session, result_id: UUID, now: datetime) -> dict | None:
    row = db.scalar(
        select(ClassifierEvaluation).where(ClassifierEvaluation.id == result_id, ClassifierEvaluation.expires_at > now)
    )
    return row.result if row else None


def clear_results(db: Session) -> None:
    db.execute(delete(ClassifierEvaluation))
    db.commit()
