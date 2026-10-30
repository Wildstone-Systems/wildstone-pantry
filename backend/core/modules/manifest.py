"""Module manifest type.

A module's ``manifest.py`` constructs one of these to declare what it exposes
to the platform. Kept intentionally small; richer metadata (owns/consumes,
lifecycle) lives in each module's ``_meta.py``.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field

from fastapi import APIRouter


@dataclass(frozen=True)
class ModuleManifest:
    name: str
    version: str = "0.1.0"
    routers: Sequence[APIRouter] = field(default_factory=tuple)
    # Long-running loops started with the app and cancelled at shutdown. Each
    # must swallow its own failures: one bad tick must never take the app down.
    background: Sequence[Callable[[], Awaitable[None]]] = field(default_factory=tuple)
