"""Automation module manifest.

Automation exposes OCR / receipt + recipe parsing / expiration estimation as
services consumed by other modules, plus the AI provider seam (ADR 0018 §9):
provider status and the cloud-AI privacy consent live at /api/ai/*.
"""

from core.modules import ModuleManifest, register_module
from modules.automation.api import ai_router
from modules.automation.api.classifier import router as classifier_router
from modules.automation.api.benchmark import router as benchmark_router

manifest = register_module(
    ModuleManifest(name="automation", version="0.1.0", routers=[ai_router, classifier_router, benchmark_router])
)
