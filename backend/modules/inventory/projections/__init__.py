"""Pure inventory projection logic (ADR 0002).

These functions fold the event stream into read models. They are intentionally
DB-free and side-effect-free (stdlib + Decimal only) so they unit-test directly
and can be reused by the synchronous projection updater and the offline rebuild.
"""

from modules.inventory.projections.balance import BalanceKey, replay_balances
from modules.inventory.projections.event_data import EventData, balance_delta

__all__ = ["EventData", "balance_delta", "BalanceKey", "replay_balances"]
