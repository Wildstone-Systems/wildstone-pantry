"""Shopping module metadata."""

MODULE_INFO = {
    "name": "shopping",
    "purpose": "Purchase CSV import, receipt text/image ingestion, email receipts.",
    "owns": ["purchases", "purchase_line_items", "receipts", "receipt_line_items"],
    "consumes_from": {"catalog": ["items", "barcodes"], "inventory": ["add_inventory"]},
    "invariants": ["Imports are idempotent via purchase fingerprint dedupe."],
}
