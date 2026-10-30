"""Public explicit-save and read DTO interfaces for reusable product facts."""

from datetime import UTC, datetime

from modules.catalog import product_evidence_repository as repository
from modules.catalog.schemas.product_evidence import ProductEvidenceRead


def describe(db, item_id, base_source_hash):
    row = repository.get(db, item_id)
    return ProductEvidenceRead(
        item_id=item_id,
        revision=row.revision if row else 0,
        base_source_hash=base_source_hash,
        saved_base_source_hash=row.base_source_hash if row else None,
        facts=row.facts if row else {},
        stale=bool(row and row.base_source_hash != base_source_hash),
        updated_by=row.updated_by if row else None,
        updated_at=row.updated_at if row else None,
    ).model_dump(mode="json", exclude_none=False)


def read(db, item_id):
    from modules.catalog.services.match_evidence import item_base_evidence

    base = item_base_evidence(db, item_id)
    return describe(db, item_id, base["source_fingerprint"])


def save(db, item_id, command, actor_id):
    from modules.catalog.services.match_evidence import item_base_evidence

    # Source item serializes the first metadata insert as well as later updates.
    # The same catalog lock is used by pending recipe confirmation.
    base = item_base_evidence(db, item_id, lock=True)
    current = describe(db, item_id, base["source_fingerprint"])
    if command.base_source_hash != current["base_source_hash"] or command.expected_revision != current["revision"]:
        db.rollback()
        raise ValueError("Product source or saved facts changed")
    repository.replace(
        db,
        item_id=item_id,
        expected_revision=command.expected_revision,
        base_source_hash=command.base_source_hash,
        facts=command.facts.model_dump(exclude_none=True),
        actor_id=actor_id,
        now=datetime.now(UTC),
    )
    return read(db, item_id)
