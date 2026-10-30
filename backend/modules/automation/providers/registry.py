"""AI provider registry (ADR 0018 §9), mirroring the connector registry.

Resolution order is the owner's Q47-2 decision: a configured LOCAL provider
always wins (self-host ethos, zero cloud cost); the cloud provider serves
entitled+consented households with nothing local configured. No provider →
None, and callers degrade to the deterministic parsers.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from modules.automation.providers.base import AIProvider
from modules.automation.providers.local import OllamaProvider, OpenAICompatProvider
from modules.automation.providers.wildstone import WildstoneProvider

# Local providers first — local wins by default (Q47-2).
_PROVIDERS: list[AIProvider] = [OllamaProvider(), OpenAICompatProvider(), WildstoneProvider()]


def list_providers() -> list[AIProvider]:
    return list(_PROVIDERS)


def resolve_provider(db: Session, capability: str) -> AIProvider | None:
    for provider in _PROVIDERS:
        if capability in provider.capabilities() and provider.configured(db):
            return provider
    return None
