#!/usr/bin/env python3
"""Backfill the inventory event store + projections from legacy tables (ADR 0002).

Idempotent: does nothing if events already exist (pass --force to rebuild from
the existing log). Run AFTER applying migration 0012_inventory_events.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db.session import SessionLocal  # noqa: E402
from modules.inventory.backfill import backfill_from_legacy  # noqa: E402


def main() -> int:
    force = "--force" in sys.argv[1:]
    with SessionLocal() as db:
        summary = backfill_from_legacy(db, force=force)
        db.commit()
    print("inventory backfill:", summary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
