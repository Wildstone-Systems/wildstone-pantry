from modules.catalog.api.barcodes import router as barcodes_router
from modules.catalog.api.ingredient_reference import router as ingredient_reference_router
from modules.catalog.api.items import router as items_router
from modules.catalog.api.locations import router as locations_router

__all__ = ["items_router", "locations_router", "barcodes_router", "ingredient_reference_router"]
