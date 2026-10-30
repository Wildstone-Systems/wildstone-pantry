"""Wildstone Home Lab integration module manifest (ADR 0026)."""

from core.modules import ModuleManifest, register_module
from modules.wildstone.api import admin_router, contract_router, pairing_router
from modules.wildstone.services.runner import relay_loop

manifest = register_module(
    ModuleManifest(
        name="wildstone",
        version="0.1.0",
        routers=[admin_router, contract_router, pairing_router],
        background=[relay_loop],
    )
)
