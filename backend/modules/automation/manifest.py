"""Automation module manifest.

Automation exposes OCR / recipe parsing / expiration estimation as services
consumed by other modules, plus the classifier and its benchmark.
"""

from core.modules import ModuleManifest, register_module
from modules.automation.api.classifier import router as classifier_router
from modules.automation.api.benchmark import router as benchmark_router

manifest = register_module(
    ModuleManifest(name="automation", version="0.1.0", routers=[classifier_router, benchmark_router])
)
