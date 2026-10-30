"""Agent module metadata."""

MODULE_INFO = {
    "name": "agent",
    "purpose": "Agent-facing orchestration: turn a product URL into catalog + shopping/inventory entries.",
    "owns": [],
    "consumes_from": {
        "catalog": ["create_item", "find_item_by_name"],
        "shopping": ["add_shopping_item"],
        "inventory": ["add_inventory"],
    },
    "invariants": ["No tables owned; orchestrates other modules via their service interfaces."],
}
