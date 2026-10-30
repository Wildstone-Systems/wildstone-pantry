"""Agent module manifest."""

from core.modules import ModuleManifest, register_module
from modules.agent.api import ingest_router, summary_router

manifest = register_module(
    ModuleManifest(name="agent", version="0.1.0", routers=[ingest_router, summary_router])
)
