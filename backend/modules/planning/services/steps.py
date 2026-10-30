"""Split a recipe's instruction text into walkthrough steps (ADR 0022 §3).

The ONE splitter (ADR 0026 §6.1). It began as a port of the cook-along page's
TypeScript splitter; the page now shows the session's steps, so the TypeScript
copy is gone and an agent saying "step 3" and the screen showing step 3 cannot
disagree. Its cases live in ``tests/test_cook_sessions.py``.

Precedence — the first rule that yields 2+ steps wins:
  1. sequentially numbered lines ("1.", "2)", "Step 3:", or bare "4 ")
  2. blank-line-separated paragraphs
  3. single newlines
  4. fallback: the whole text as one step

A numbered line only opens a step when its number is exactly the next one
expected; anything else is continuation text of the step above. That keeps a
scraped ingredient line like "1 ½ lb striploin steak" out of the numbering.
Pure: no DB, no I/O.
"""

from __future__ import annotations

import re

# The delimiter is optional so "1 Peel and mince" is recognised, but a
# delimiter-less match is only trusted when it continues the sequence.
_NUMBERED_LINE = re.compile(r"^\s*(?:step\s*)?(\d+)\s*([.):])?\s+(.+)$", re.IGNORECASE)
_BLANK_LINE = re.compile(r"\r?\n\s*\r?\n")
_WRAPPED = re.compile(r"\s*\r?\n\s*")


def _parse_marker(line: str) -> tuple[int, bool, str] | None:
    match = _NUMBERED_LINE.match(line)
    if not match:
        return None
    return int(match.group(1)), bool(match.group(2)), match.group(3).strip()


def _strip_marker(line: str) -> str:
    """Strip a leading "1." / "2)" / "Step 3:" marker from a one-off line."""
    marker = _parse_marker(line)
    return marker[2] if marker and marker[1] else line.strip()


def _by_sequential_numbering(lines: list[str]) -> list[str]:
    steps: list[str] = []
    expected = 1
    for line in lines:
        if not line.strip():
            continue
        marker = _parse_marker(line)
        if marker and marker[0] == expected:
            steps.append(marker[2])
            expected += 1
        elif steps:
            # Out-of-sequence number, or no number at all: continuation text.
            steps[-1] = f"{steps[-1]} {line.strip()}".strip()
        # A line before step 1 that isn't step 1 is preamble — dropped.
    return [step for step in steps if step]


def split_instructions(instructions: str | None) -> list[str]:
    text = (instructions or "").strip()
    if not text:
        return []

    lines = re.split(r"\r?\n", text)

    numbered = _by_sequential_numbering(lines)
    if len(numbered) >= 2:
        return numbered

    paragraphs = [
        stripped
        for stripped in (_strip_marker(_WRAPPED.sub(" ", p).strip()) for p in _BLANK_LINE.split(text))
        if stripped
    ]
    if len(paragraphs) >= 2:
        return paragraphs

    singles = [stripped for stripped in (_strip_marker(line.strip()) for line in lines) if stripped]
    if len(singles) >= 2:
        return singles

    whole = _strip_marker(_WRAPPED.sub(" ", text).strip())
    return [whole] if whole else []
