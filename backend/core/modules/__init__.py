"""Module registry kernel.

Feature modules expose a ``ModuleManifest`` (name, version, routers, optional
startup hook). The app factory discovers manifests and mounts their routers,
so adding a module never means editing ``app/main.py``.
"""

from core.modules.manifest import ModuleManifest
from core.modules.registry import mount_modules, register_module, registered_modules, start_background_tasks

__all__ = [
    "ModuleManifest",
    "register_module",
    "registered_modules",
    "mount_modules",
    "start_background_tasks",
]
