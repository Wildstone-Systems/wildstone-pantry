"""Shopping module metadata."""

MODULE_INFO = {
    "name": "shopping",
    "purpose": "Shopping list, store hand-off, purchase history, retailer connectors.",
    "owns": ["purchases", "purchase_line_items", "receipts", "receipt_line_items"],
    "consumes_from": {"catalog": ["items", "barcodes"], "inventory": ["add_inventory"]},
    "invariants": ["Imports are idempotent via purchase fingerprint dedupe."],
}
