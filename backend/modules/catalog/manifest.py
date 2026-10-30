"""Catalog module manifest."""

from core.modules import ModuleManifest, register_module
from modules.catalog.api import (
    barcodes_router,
    ingredient_reference_router,
    items_router,
    locations_router,
)
from modules.catalog.api.product_evidence import router as product_evidence_router

manifest = register_module(
    ModuleManifest(
        name="catalog",
        version="0.1.0",
        routers=[product_evidence_router, items_router, locations_router, barcodes_router, ingredient_reference_router],
    )
)
