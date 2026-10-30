"""AI provider contracts (ADR 0018 §9).

Structured output with provenance is the point: every line an AI produces
carries ``source`` and ``confidence``, and the one governing rule is that AI
output is ALWAYS a draft — the human preview→commit step is the only path to
the ledger (the ``_meta.py`` invariant, satisfied without a new approval
subsystem). Deterministic parsers implement the same contracts and are never
removed; providers are a fallback/enhancement, not a dependency.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from sqlalchemy.orm import Session

SOURCE_RULES = "rules"
SOURCE_AI = "ai"

CAP_VISION = "vision"

# A receipt photo any bigger than this is refused before it reaches a model.
MAX_IMAGE_BYTES = 8 * 1024 * 1024


class AIUnavailable(RuntimeError):
    """No provider can serve this request — with a human-readable reason."""


@dataclass(frozen=True)
class ReceiptLine:
    name: str
    quantity: str = "1"
    unit: str = "each"
    raw_text: str = ""
    source: str = SOURCE_AI
    confidence: float | None = None


@dataclass(frozen=True)
class ReceiptExtraction:
    provider: str
    lines: list[ReceiptLine] = field(default_factory=list)


@runtime_checkable
class AIProvider(Protocol):
    key: str
    label: str

    def configured(self, db: Session) -> bool: ...

    def capabilities(self) -> set[str]: ...

    def extract_receipt(self, db: Session, image: bytes, *, media_type: str) -> ReceiptExtraction:
        """Receipt photo -> structured draft lines. Raises AIUnavailable."""
        ...


def parse_lines_payload(provider: str, payload: dict) -> ReceiptExtraction:
    """Shared, defensive mapping of a model's ``{"lines": [...]}`` JSON into
    the contract. Garbage entries are dropped, never propagated."""
    lines: list[ReceiptLine] = []
    for entry in payload.get("lines", []):
        if not isinstance(entry, dict):
            continue
        name = str(entry.get("name", "")).strip()
        if not name:
            continue
        confidence = entry.get("confidence")
        try:
            confidence = None if confidence is None else max(0.0, min(1.0, float(confidence)))
        except (TypeError, ValueError):
            confidence = None
        lines.append(
            ReceiptLine(
                name=name[:200],
                quantity=str(entry.get("quantity", "1"))[:20] or "1",
                unit=str(entry.get("unit", "each"))[:20] or "each",
                raw_text=str(entry.get("raw_text", ""))[:300],
                source=SOURCE_AI,
                confidence=confidence,
            )
        )
    return ReceiptExtraction(provider=provider, lines=lines)


RECEIPT_PROMPT = (
    "You are reading a grocery receipt photo. Extract the purchased line items. "
    'Respond with ONLY a JSON object: {"lines": [{"name": str, "quantity": str, '
    '"unit": str, "raw_text": str, "confidence": number 0-1}]}. '
    "Use the item's plain product name (not store codes), quantity as a decimal "
    "string, unit as one of: each, lb, oz, kg, g, l, ml. Skip totals, tax, "
    "coupons, and non-item lines."
)
