"""Only shopping-owned purchase/draft/claim tables are accessed here."""

from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import delete, select, text, update
from sqlalchemy.exc import IntegrityError

from app.models.purchase import Purchase
from app.models.purchase_line_item import PurchaseLineItem
from modules.shopping.purchase_review_models import PurchaseCsvClaim, PurchaseReviewDraft


def existing_purchase(db, location_id, fingerprint):
    claim = db.get(PurchaseCsvClaim, (location_id, fingerprint))
    if claim and claim.purchase_id:
        return claim.purchase_id
    return db.scalar(
        select(Purchase.id)
        .where(
            Purchase.location_id == location_id, Purchase.source == "csv", Purchase.source_fingerprint == fingerprint
        )
        .order_by(Purchase.created_at, Purchase.id)
        .limit(1)
    )


def claim_source(db, location_id, fingerprint):
    existing = existing_purchase(db, location_id, fingerprint)
    if existing:
        raise ValueError(f"Duplicate import (purchase_id={existing})")
    row = PurchaseCsvClaim(location_id=location_id, source_fingerprint=fingerprint, purchase_id=None)
    db.add(row)
    try:
        db.flush()
    except IntegrityError as exc:
        raise ValueError("This CSV source is already being imported; refresh purchase history") from exc
    return row


def create_draft(db, *, actor_id, token_hash, expires_at, document):
    if db.get_bind().dialect.name == "postgresql":
        db.execute(text("LOCK TABLE purchase_review_drafts IN EXCLUSIVE MODE"))
    db.execute(delete(PurchaseReviewDraft).where(PurchaseReviewDraft.expires_at <= datetime.now(UTC)))
    row = PurchaseReviewDraft(
        id=uuid4(), actor_id=actor_id, token_hash=token_hash, expires_at=expires_at, revision=0, document=document
    )
    db.add(row)
    db.flush()
    excess = db.scalars(
        select(PurchaseReviewDraft.id)
        .order_by(PurchaseReviewDraft.expires_at.desc(), PurchaseReviewDraft.id.desc())
        .offset(100)
    ).all()
    if excess:
        db.execute(delete(PurchaseReviewDraft).where(PurchaseReviewDraft.id.in_(excess)))
    db.commit()
    return row


def read_draft(db, draft_id, actor_id, token_hash):
    return db.scalar(
        select(PurchaseReviewDraft).where(
            PurchaseReviewDraft.id == draft_id,
            PurchaseReviewDraft.actor_id == actor_id,
            PurchaseReviewDraft.token_hash == token_hash,
        )
    )


def locked_draft(db, draft_id, actor_id, token_hash):
    result = db.execute(
        update(PurchaseReviewDraft)
        .where(
            PurchaseReviewDraft.id == draft_id,
            PurchaseReviewDraft.actor_id == actor_id,
            PurchaseReviewDraft.token_hash == token_hash,
        )
        .values(revision=PurchaseReviewDraft.revision + 1)
    )
    if result.rowcount != 1:
        raise LookupError("Purchase draft not found")
    return db.scalar(
        select(PurchaseReviewDraft).where(PurchaseReviewDraft.id == draft_id).execution_options(populate_existing=True)
    )


def new_purchase(db, **values):
    row = Purchase(**values)
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def new_line(db, **values):
    row = PurchaseLineItem(**values)
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def complete_purchase(db, row, count):
    row.status = "processed"
    row.line_count = count
    db.add(row)
    db.commit()
    db.refresh(row)
