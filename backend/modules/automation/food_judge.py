"""Ask a local model one question: are these two names the same food?

Scoped deliberately narrowly. Measured against a live Ollama endpoint, a small
local model scored 10/10 on exactly this judgment — including every one of six
lexical near-misses it had to reject (``bell pepper`` vs "Black pepper grinder
refill", ``chili powder`` vs "Baking Powder", ``canned tomatoes`` vs "Canned
Tuna") — in under four seconds for the whole batch. On the neighbouring task of
recalling a physical constant it was 11–57% wrong. So this module asks only the
former, and the density itself always comes from USDA.

Why the judgment is worth a model at all: generating candidates is easy and
generating *good* ones is not. Lexical overlap produces 85 candidates across 149
unmatched ingredients and most are nonsense; USDA's own search for "salt"
returns "Pecans, salted" first. In both cases the useful work is **rejection**,
which is semantic and has no string-distance equivalent.

Two properties this relies on and enforces:

- **Verification, never authorship.** The model is only ever shown two names and
  asked whether they agree. It cannot invent a food, a number or a match.
- **Refusal is the safe default.** Anything that fails to parse, times out, or
  comes back unconfigured resolves to "not verified" — which downgrades the
  suggestion to needing a human, exactly where it started.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass

import httpx
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

_TIMEOUT = 45.0

# Constrained decoding — the endpoint enforces the shape, so a malformed reply
# is impossible rather than merely unlikely.
_VERDICT_SCHEMA = {
    "type": "object",
    "properties": {
        "same_food": {"type": "boolean"},
        "why": {"type": "string"},
    },
    "required": ["same_food", "why"],
}

_PROMPT = (
    "A home pantry holds a product called {product!r}.\n"
    "A food reference database has an entry called {reference!r}.\n\n"
    "Are these the same food — would measurements published for the database "
    "entry (how much a teaspoon of it weighs) be correct for the pantry "
    "product?\n\n"
    "Judge the EDIBLE CONTENTS only. Words about the container, the brand or "
    "the size ('refill', 'grinder', 'jar', 'Great Value', '8 oz') say nothing "
    "about what the food is — a 'black pepper grinder refill' IS black pepper. "
    "A more specific entry still matches a general one: 'iodized salt' is "
    "'Salt, table'.\n\n"
    "But be strict about the food itself. Sharing a word is not enough: "
    "'Canned Tuna' is not 'canned tomatoes', and 'Baking Powder' is not "
    "'chili powder'.\n\n"
    'Answer JSON: {{"same_food": true|false, "why": "<at most 10 words>"}}'
)


@dataclass(frozen=True)
class FoodVerdict:
    same_food: bool
    why: str
    # False when no model was consulted at all, so callers can tell "the model
    # said no" apart from "nobody asked" — they mean very different things for
    # whether a human still needs to look.
    checked: bool = True

    @classmethod
    def unchecked(cls, why: str = "no local model configured") -> FoodVerdict:
        return cls(same_food=False, why=why, checked=False)


def _ollama_config(db: Session) -> dict:
    from modules.shopping.services.connector_config import get_effective

    try:
        cfg = get_effective(db, "ollama")
    except Exception:  # noqa: BLE001 — unconfigured/undecryptable means dormant
        return {}
    return cfg if cfg.get("base_url") and cfg.get("model") else {}


def available(db: Session) -> bool:
    """Whether a model is configured. Nothing here makes an HTTP call until so."""
    return bool(_ollama_config(db))


def same_food(db: Session, *, product: str, reference: str) -> FoodVerdict:
    """Is ``product`` the same food as ``reference``?

    Never raises. Every failure path — unconfigured, unreachable, timeout,
    unparseable — returns ``checked=False``, which callers must treat as "still
    needs a human", not as a rejection.
    """
    cfg = _ollama_config(db)
    if not cfg:
        return FoodVerdict.unchecked()

    from modules.catalog.services.reference_budget import transport_timeout

    prompt = _PROMPT.format(product=(product or "").strip(), reference=(reference or "").strip())
    try:
        response = httpx.post(
            f"{cfg['base_url'].rstrip('/')}/api/generate",
            timeout=transport_timeout(_TIMEOUT),
            json={
                "model": cfg["model"],
                "prompt": prompt,
                "stream": False,
                # Deterministic: the same pair must not flip verdict between runs.
                "options": {"temperature": 0},
                "format": _VERDICT_SCHEMA,
            },
        )
        response.raise_for_status()
        payload = json.loads(response.json().get("response") or "{}")
    except (httpx.HTTPError, ValueError, KeyError, TimeoutError) as exc:
        logger.info("food judge unavailable: %s", type(exc).__name__)
        return FoodVerdict.unchecked(f"model unavailable ({type(exc).__name__})")

    if not isinstance(payload.get("same_food"), bool):
        return FoodVerdict.unchecked("model returned no verdict")

    return FoodVerdict(
        same_food=payload["same_food"],
        why=str(payload.get("why") or "").strip()[:120],
    )
