"""Catalog module: product identity, barcodes, and storage locations.

Owns the item/category/barcode/location-facing API, services, and schemas.
ORM models remain in the shared ``app.models`` registry during the migration.

NOTE: this package __init__ is intentionally side-effect free. The manifest is
registered by importing ``modules.catalog.manifest`` (done in ``modules``), so
importing ``modules.catalog.services`` never pulls in the API/security layer.
"""
