"""Public stock revision snapshot for explicit identity confirmation."""

from modules.inventory import review_snapshot_repository


def snapshot(db, *, lock=False):
    return review_snapshot_repository.snapshot(db, lock=lock)


def stocked_item_ids(db):
    return review_snapshot_repository.stocked_item_ids(db)
