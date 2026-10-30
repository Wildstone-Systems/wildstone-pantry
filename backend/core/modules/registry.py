"""Module registration + mounting.

Modules register a manifest (typically from their ``manifest.py`` at import
time); the app factory calls ``mount_modules(app)`` to include every router.
"""

from __future__ import annotations

import asyncio

from fastapi import FastAPI

from core.modules.manifest import ModuleManifest

_REGISTRY: dict[str, ModuleManifest] = {}


def register_module(manifest: ModuleManifest) -> ModuleManifest:
    _REGISTRY[manifest.name] = manifest
    return manifest


def registered_modules() -> list[ModuleManifest]:
    return list(_REGISTRY.values())


def mount_modules(app: FastAPI, *, api_prefix: str = "/api") -> None:
    """Mount every registered module's routers onto the app.

    Local features are never gated app-wide (ADR 0018); auth and entitlement
    dependencies live on individual routes.
    """
    for manifest in _REGISTRY.values():
        for router in manifest.routers:
            app.include_router(router, prefix=api_prefix)


def start_background_tasks() -> list[asyncio.Task]:
    """Start every registered module's background loops (call from lifespan)."""
    loop = asyncio.get_running_loop()
    return [
        loop.create_task(job(), name=f"{manifest.name}:{getattr(job, '__name__', 'job')}")
        for manifest in _REGISTRY.values()
        for job in manifest.background
    ]
