"""Catalog module metadata."""

MODULE_INFO = {
    "name": "catalog",
    "purpose": "Product/item identity, barcode lookup + aliasing, storage locations.",
    "owns": ["items", "categories", "barcode_aliases", "inventory_locations"],
    "consumes_from": {},
    "invariants": [
        "Barcodes are normalized before lookup/storage.",
        "Item identity is stable; packaging/pricing live elsewhere (future).",
    ],
}
