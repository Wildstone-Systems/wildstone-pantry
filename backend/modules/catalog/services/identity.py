"""Catalog-owned identity writes. Keys describe ingredient equivalence, never SKU merges."""
from datetime import UTC, datetime

from sqlalchemy import update
from sqlalchemy.orm import Session

from app.models import Item


class IdentityConflict(ValueError):
    pass


def change_identity(db: Session, item: Item, *, key: str | None, version: int | None,
                    provenance: str, expected_key: str | None = None,
                    expected_revision: int | None = None, check_key: bool = False) -> None:
    if (check_key and item.canonical_ingredient != expected_key) or (
        expected_revision is not None and item.canonical_revision != expected_revision
    ):
        raise IdentityConflict("Identity changed since review. Refresh and review again.")
    revision = item.canonical_revision
    # Sessions disable autoflush. Preserve other pending catalog edits before
    # refreshing the row after the conditional identity update; still one
    # transaction, so a failed comparison rolls these edits back as well.
    db.flush()
    result = db.execute(update(Item).where(Item.id == item.id, Item.canonical_revision == revision).values(
        canonical_ingredient=key, canonical_version=version, canonical_provenance=provenance,
        canonical_revision=revision + 1,
        canonical_confirmed_at=datetime.now(UTC) if provenance == "confirmed" else None,
    ).execution_options(synchronize_session=False))
    if result.rowcount != 1:
        raise IdentityConflict("Identity changed since review. Refresh and review again.")
    db.refresh(item)


def confirm_identity(db: Session, *, item_id, key: str | None, version: int | None,
                     replace_confirmed: bool = False, expected_key: str | None = None,
                     expected_revision: int | None = None, check_key: bool = False) -> Item:
    item = db.get(Item, item_id)
    if item is None or not item.is_active:
        raise LookupError("Active item not found")
    if item.canonical_provenance == "confirmed" and item.canonical_ingredient != key:
        if not replace_confirmed or not check_key or expected_revision is None:
            raise IdentityConflict("This product already has a confirmed identity. Explicitly review its replacement.")
    change_identity(db, item, key=key, version=version, provenance="confirmed",
                    expected_key=expected_key, expected_revision=expected_revision, check_key=check_key)
    return item
