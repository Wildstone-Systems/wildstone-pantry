"""Inventory module manifest."""

from core.modules import ModuleManifest, register_module
from modules.inventory.api import consumption_router, inventory_router

manifest = register_module(
    ModuleManifest(name="inventory", version="0.1.0", routers=[inventory_router, consumption_router])
)
