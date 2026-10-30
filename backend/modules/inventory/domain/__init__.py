"""Pure inventory domain logic (no DB, no HTTP) — ADR 0002."""

from modules.inventory.domain.lots import Allocation, Lot, allocate_fefo, replay_lots

__all__ = ["Lot", "Allocation", "allocate_fefo", "replay_lots"]
