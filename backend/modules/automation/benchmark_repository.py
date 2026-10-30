"""Automation-owned bounded synthetic evidence records, optimistic concurrency."""

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy import delete, select, text, update
from sqlalchemy.orm import Session

from modules.automation.models import ClassifierBenchmark

REVIEW_ID = UUID("00000000-0000-0000-0000-000000000094")


def read(db: Session, key: UUID):
    row = db.get(ClassifierBenchmark, key)
    if row is None or row.expires_at.replace(tzinfo=UTC) <= datetime.now(UTC):
        return None
    return row


def compatibility_state(db: Session, old_marker: UUID, new_marker: UUID, *, lock: bool = False):
    if lock:
        if db.get_bind().dialect.name == "postgresql":
            db.execute(text("LOCK TABLE classifier_benchmarks IN EXCLUSIVE MODE"))
        else:
            # Obtain SQLite's writer reservation before inspecting any state.
            db.execute(
                text("UPDATE classifier_benchmarks SET revision = revision WHERE id = :key"), {"key": REVIEW_ID.hex}
            )
        db.expire_all()
    return {
        "review": db.get(ClassifierBenchmark, REVIEW_ID),
        "old_marker": db.get(ClassifierBenchmark, old_marker),
        "new_marker": db.get(ClassifierBenchmark, new_marker),
        "retained_evidence": len(
            db.scalars(
                select(ClassifierBenchmark.id).where(
                    ClassifierBenchmark.kind.in_(["run", "review_history", "draft_upgrade"])
                )
            ).all()
        ),
        "marker_count": len(
            db.scalars(select(ClassifierBenchmark.id).where(ClassifierBenchmark.kind == "holdout_marker")).all()
        ),
    }


def replace_unreviewed_draft(db: Session, row, document: dict, marker_id: UUID, receipt: dict):
    revision = row.revision
    changed = db.execute(
        update(ClassifierBenchmark)
        .where(ClassifierBenchmark.id == REVIEW_ID, ClassifierBenchmark.revision == revision)
        .values(document=document, revision=revision + 1)
        .execution_options(synchronize_session=False)
    )
    if changed.rowcount != 1:
        db.rollback()
        raise ValueError("Draft changed during upgrade; refresh")
    now = datetime.now(UTC)
    if db.get(ClassifierBenchmark, marker_id) is None:
        db.add(
            ClassifierBenchmark(
                id=marker_id,
                kind="holdout_marker",
                created_at=now,
                expires_at=datetime(9999, 1, 1, tzinfo=UTC),
                revision=0,
                document={"corpus_hash": document["corpus_hash"], "exposed": False},
            )
        )
    db.add(
        ClassifierBenchmark(
            id=uuid4(),
            kind="draft_upgrade",
            created_at=now,
            expires_at=datetime(9999, 1, 1, tzinfo=UTC),
            revision=0,
            document=receipt,
        )
    )
    db.commit()
    db.expire_all()


def _write_lock(db):
    if db.get_bind().dialect.name == "postgresql":
        db.execute(text("LOCK TABLE classifier_benchmarks IN EXCLUSIVE MODE"))
    else:
        db.execute(text("UPDATE classifier_benchmarks SET revision = revision WHERE id = :key"), {"key": REVIEW_ID.hex})
    db.expire_all()


def diagnostic_freeze(db, row, document, marker_id, authorization_hash):
    """CAS review + minimal permanent one-allocation marker, in one transaction."""
    expected_revision = row.revision
    _write_lock(db)
    marker = db.get(ClassifierBenchmark, marker_id)
    current = read(db, REVIEW_ID)
    if (
        current is None
        or current.revision != expected_revision
        or marker is None
        or marker.document.get("exposed") is not False
        or marker.document.get("diagnostic")
    ):
        db.rollback()
        raise ValueError("Diagnostic allocation already used or evidence changed")
    marker.document = {
        **marker.document,
        "diagnostic": {"authorization_hash": authorization_hash, "split_used": {"train": False, "holdout": False}},
    }
    marker.revision += 1
    current.document = document
    current.revision += 1
    db.commit()
    db.expire_all()


def create(db: Session, kind: str, document: dict, *, key: UUID | None = None, diagnostic_claim=None):
    now = datetime.now(UTC)
    if db.get_bind().dialect.name == "postgresql":
        db.execute(text("LOCK TABLE classifier_benchmarks IN EXCLUSIVE MODE"))
    if diagnostic_claim is not None:
        marker_id, authorization_hash, split, review_revision = diagnostic_claim
        _write_lock(db)
        marker = db.get(ClassifierBenchmark, marker_id)
        current = read(db, REVIEW_ID)
        allocation = marker.document.get("diagnostic") if marker else None
        if (
            current is None
            or current.revision != review_revision
            or not allocation
            or allocation.get("authorization_hash") != authorization_hash
            or allocation["split_used"][split]
        ):
            db.rollback()
            raise ValueError("Diagnostic split already allocated or snapshot changed; no reruns")
        marker.document = {
            **marker.document,
            "diagnostic": {**allocation, "split_used": {**allocation["split_used"], split: True}},
        }
        marker.revision += 1
    db.execute(
        delete(ClassifierBenchmark)
        .where(ClassifierBenchmark.expires_at <= now)
        .execution_options(synchronize_session=False)
    )
    if kind in {"run", "review_history", "holdout_marker"}:
        # Never evict an active request's record. Refuse creation at the cap.
        count = len(db.scalars(select(ClassifierBenchmark.id).where(ClassifierBenchmark.kind == kind)).all())
        if count >= (100 if kind == "holdout_marker" else 20):
            db.rollback()
            raise ValueError("Run retention limit reached; delete an old run first")
    row = ClassifierBenchmark(
        id=key or uuid4(),
        kind=kind,
        created_at=now,
        expires_at=datetime(9999, 1, 1, tzinfo=UTC) if kind == "holdout_marker" else now + timedelta(days=30),
        revision=0,
        document=document,
    )
    db.add(row)
    db.commit()
    return row


def change(db: Session, row, document: dict, *, expected_revision: int | None = None):
    revision = row.revision if expected_revision is None else expected_revision
    changed = db.execute(
        update(ClassifierBenchmark)
        .where(ClassifierBenchmark.id == row.id, ClassifierBenchmark.revision == revision)
        .values(document=document, revision=revision + 1)
        .execution_options(synchronize_session=False)
    )
    if changed.rowcount != 1:
        db.rollback()
        raise ValueError("Evidence changed; refresh and try again")
    db.commit()
    db.expire_all()


def archive_review(db: Session, row, document: dict, archive: dict):
    """One transaction: bounded immutable receipt plus optimistic review transition."""
    now = datetime.now(UTC)
    if db.get_bind().dialect.name == "postgresql":
        db.execute(text("LOCK TABLE classifier_benchmarks IN EXCLUSIVE MODE"))
    db.execute(
        delete(ClassifierBenchmark)
        .where(ClassifierBenchmark.expires_at <= now)
        .execution_options(synchronize_session=False)
    )
    count = len(db.scalars(select(ClassifierBenchmark.id).where(ClassifierBenchmark.kind == "review_history")).all())
    if count >= 20:
        db.rollback()
        raise ValueError("Review history retention cap reached")
    changed = db.execute(
        update(ClassifierBenchmark)
        .where(ClassifierBenchmark.id == row.id, ClassifierBenchmark.revision == row.revision)
        .values(document=document, revision=row.revision + 1)
        .execution_options(synchronize_session=False)
    )
    if changed.rowcount != 1:
        db.rollback()
        raise ValueError("Review changed; refresh")
    db.add(
        ClassifierBenchmark(
            id=uuid4(),
            kind="review_history",
            created_at=now,
            expires_at=now + timedelta(days=30),
            revision=0,
            document=archive,
        )
    )
    db.commit()
    db.expire_all()


def recent(db: Session):
    return db.scalars(
        select(ClassifierBenchmark)
        .where(ClassifierBenchmark.kind == "run", ClassifierBenchmark.expires_at > datetime.now(UTC))
        .order_by(ClassifierBenchmark.created_at.desc())
        .limit(20)
    ).all()


def remove(db: Session, key: UUID):
    row = read(db, key)
    if row is None or row.kind != "run" or row.document["status"] == "running":
        raise ValueError("Run unavailable or still running")
    removed = db.execute(
        delete(ClassifierBenchmark)
        .where(ClassifierBenchmark.id == key, ClassifierBenchmark.revision == row.revision)
        .execution_options(synchronize_session=False)
    )
    if removed.rowcount != 1:
        db.rollback()
        raise ValueError("Run changed or became active; refresh")
    db.commit()
