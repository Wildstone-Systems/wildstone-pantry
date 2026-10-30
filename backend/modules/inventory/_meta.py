"""Inventory module metadata."""

MODULE_INFO = {
    "name": "inventory",
    "purpose": "Stock add/remove/adjust/move, transactions, and location queries.",
    "owns": ["inventory", "inventory_transactions"],
    "consumes_from": {"catalog": ["items", "locations"]},
    "invariants": ["Every mutation writes an inventory transaction row."],
}
