"""AI provider registry (ADR 0018 §9), mirroring the connector registry.

Resolution: the first configured local provider
is used (self-host ethos, zero cloud cost). No provider →
None, and callers degrade to the deterministic parsers.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from modules.automation.providers.base import AIProvider
from modules.automation.providers.local import OllamaProvider, OpenAICompatProvider

# Local providers first — local wins by default (Q47-2).
_PROVIDERS: list[AIProvider] = [OllamaProvider(), OpenAICompatProvider()]


def list_providers() -> list[AIProvider]:
    return list(_PROVIDERS)


def resolve_provider(db: Session, capability: str) -> AIProvider | None:
    for provider in _PROVIDERS:
        if capability in provider.capabilities() and provider.configured(db):
            return provider
    return None
