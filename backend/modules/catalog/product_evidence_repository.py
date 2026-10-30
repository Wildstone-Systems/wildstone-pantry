"""Catalog-owned reusable facts; caller locks the source item before replacement."""

from sqlalchemy import select, update

from modules.catalog.models import ItemProductEvidence


def get(db, item_id):
    return db.execute(
        select(ItemProductEvidence)
        .where(ItemProductEvidence.item_id == item_id)
        .execution_options(populate_existing=True)
    ).scalar_one_or_none()


def replace(db, *, item_id, expected_revision, base_source_hash, facts, actor_id, now):
    values = dict(
        revision=expected_revision + 1,
        base_source_hash=base_source_hash,
        facts=facts,
        updated_by=actor_id,
        updated_at=now,
    )
    row = get(db, item_id)
    if row is None:
        if expected_revision != 0:
            raise ValueError("Product facts changed")
        db.add(ItemProductEvidence(item_id=item_id, **values))
    else:
        result = db.execute(
            update(ItemProductEvidence)
            .where(ItemProductEvidence.item_id == item_id, ItemProductEvidence.revision == expected_revision)
            .values(**values)
            .execution_options(synchronize_session=False)
        )
        if result.rowcount != 1:
            db.rollback()
            raise ValueError("Product facts changed")
    db.commit()
    db.expire_all()
