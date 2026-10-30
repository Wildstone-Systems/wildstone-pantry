"""Only shopping-owned review storage is accessed here."""

from datetime import UTC, datetime

from sqlalchemy import delete, select, text, update

from modules.shopping.receipt_review_models import ReceiptReviewDraft


def create(db, row):
    if db.get_bind().dialect.name == "postgresql":
        db.execute(text("LOCK TABLE receipt_review_drafts IN EXCLUSIVE MODE"))
    db.execute(delete(ReceiptReviewDraft).where(ReceiptReviewDraft.expires_at <= datetime.now(UTC)))
    db.add(row)
    db.flush()
    excess = db.scalars(
        select(ReceiptReviewDraft.id)
        .order_by(ReceiptReviewDraft.expires_at.desc(), ReceiptReviewDraft.id.desc())
        .offset(100)
    ).all()
    if excess:
        db.execute(delete(ReceiptReviewDraft).where(ReceiptReviewDraft.id.in_(excess)))
    db.commit()


def locked(db, draft_id, actor_id, token_hash):
    # UPDATE acquires a real SQLite write transaction and serializes PostgreSQL
    # contenders. Following SELECT observes the latest committed document.
    result = db.execute(
        update(ReceiptReviewDraft)
        .where(
            ReceiptReviewDraft.id == draft_id,
            ReceiptReviewDraft.actor_id == actor_id,
            ReceiptReviewDraft.token_hash == token_hash,
        )
        .values(revision=ReceiptReviewDraft.revision + 1)
    )
    if result.rowcount != 1:
        db.rollback()
        raise LookupError("Receipt draft not found")
    return db.scalar(
        select(ReceiptReviewDraft).where(ReceiptReviewDraft.id == draft_id).execution_options(populate_existing=True)
    )


def read(db, draft_id, actor_id, token_hash):
    return db.scalar(
        select(ReceiptReviewDraft).where(
            ReceiptReviewDraft.id == draft_id,
            ReceiptReviewDraft.actor_id == actor_id,
            ReceiptReviewDraft.token_hash == token_hash,
        )
    )
