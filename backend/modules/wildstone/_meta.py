"""Wildstone integration module metadata."""

MODULE_INFO = {
    "name": "wildstone",
    "purpose": (
        "Connect PantryOS to Wildstone Home Lab: answer the pantry-1 contract for "
        "Wildstone's adapter, and relay household events to Keystone's intake."
    ),
    "owns": ["wildstone_outbox", "wildstone_relay_state", "wildstone_stock_flags", "wildstone_member_links"],
    "consumes_from": {
        "inventory": ["feed.events_after", "feed.head_seq", "feed.household_stock"],
        "planning": ["cook_sessions", "list_expiring_items", "cook_events_since", "suggest_recipes"],
        "shopping": ["list_entries", "add_entry_once", "check_off_by_name", "items_below_threshold", "connector_config"],
        "agent": ["build_summary"],
    },
    "invariants": [
        "Outbox rows are written in the same transaction that advances the relay cursor.",
        "Event ids are deterministic over their source, so re-deriving an event is a no-op.",
        "Connecting starts at the ledger's end; it never replays history.",
        "Never on the request path: a slow Wildstone delays events, never writes.",
    ],
}
