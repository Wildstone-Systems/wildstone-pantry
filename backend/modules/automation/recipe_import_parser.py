from __future__ import annotations

import re

from modules.catalog.services.units import to_recipe_unit

_INGREDIENTS_HEADINGS = ("ingredients",)
_INSTRUCTIONS_HEADINGS = (
    "instructions",
    "directions",
    "method",
    "preparation",
    "prep",
    "steps",
    "step",
    "howtomakeit",
)

# "1." / "2)" / "(3)" — a numbered-list marker, not a quantity. Without this a
# step like "1. Preheat the oven" parses as 1 x "Preheat the oven" and lands in
# the ingredient list.
_STEP_MARKER_RE = re.compile(r"^\(?\d{1,2}[.)]$")

# A line this wordy inside the ingredients block is almost certainly a method
# step that lost its heading, not an ingredient.
_PROSE_WORD_COUNT = 9


def _clean_lines(text: str) -> list[str]:
    out: list[str] = []
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line:
            continue
        # Common OCR noise/bullets.
        line = line.lstrip("-*•").strip()
        # Collapse spaces.
        line = re.sub(r"\s+", " ", line)
        if line:
            out.append(line)
    return out


def _find_heading(lines: list[str], headings: tuple[str, ...]) -> int | None:
    for idx, line in enumerate(lines):
        normalized = re.sub(r"[^a-z]", "", line.lower())
        for heading in headings:
            if normalized == re.sub(r"[^a-z]", "", heading.lower()):
                return idx
            # "Ingredients:" etc
            if normalized.startswith(re.sub(r"[^a-z]", "", heading.lower())) and len(normalized) <= len(heading) + 2:
                return idx
    return None


_FRACTION_RE = re.compile(r"^\s*(\d+)\s*/\s*(\d+)\s*$")
_MIXED_FRACTION_RE = re.compile(r"^\s*(\d+)\s+(\d+)\s*/\s*(\d+)\s*$")


def _parse_quantity(token: str) -> float | None:
    token = (token or "").strip()
    if not token:
        return None
    if m := _MIXED_FRACTION_RE.match(token):
        whole = int(m.group(1))
        num = int(m.group(2))
        den = int(m.group(3))
        if den != 0:
            return whole + (num / den)
        return None
    if m := _FRACTION_RE.match(token):
        num = int(m.group(1))
        den = int(m.group(2))
        if den != 0:
            return num / den
        return None
    try:
        return float(token)
    except Exception:
        return None


_UNIT_ALIASES = {
    "tsp": "tsp",
    "teaspoon": "tsp",
    "teaspoons": "tsp",
    "tbsp": "tbsp",
    "tablespoon": "tbsp",
    "tablespoons": "tbsp",
    "cup": "cup",
    "cups": "cup",
    "oz": "oz",
    "ounce": "oz",
    "ounces": "oz",
    "lb": "lb",
    "pound": "lb",
    "pounds": "lb",
    "g": "g",
    "gram": "g",
    "grams": "g",
    "kg": "kg",
    "ml": "ml",
    "l": "l",
    "pinch": "pinch",
    "clove": "clove",
    "cloves": "clove",
    **{token: token for token in ("can", "cans", "package", "packages", "dash", "handful", "bunch", "bunches", "slice", "slices")},
}


def parse_recipe_text(text: str) -> dict:
    """
    Deterministic best-effort parser:
    - Tries to detect Ingredients/Instructions sections.
    - Falls back to extracting likely ingredient lines.

    Returns:
    - title: str
    - ingredients: [{name, quantity, unit, raw_line}]
    - instructions: str
    - raw_text: str
    """
    raw_text = text or ""
    lines = _clean_lines(raw_text)
    if not lines:
        return {"title": "Untitled Recipe", "ingredients": [], "instructions": "", "raw_text": raw_text}

    title = lines[0][:200]
    # If the first line is a heading, use a later line as title.
    if re.sub(r"[^a-z]", "", title.lower()) in {"ingredients", "instructions", "directions", "method"} and len(lines) > 1:
        title = lines[1][:200]

    ing_idx = _find_heading(lines, _INGREDIENTS_HEADINGS)
    ins_idx = _find_heading(lines, _INSTRUCTIONS_HEADINGS)

    ingredients_lines: list[str] = []
    instruction_lines: list[str] = []

    if ing_idx is not None and (ins_idx is None or ing_idx < ins_idx):
        start = ing_idx + 1
        end = ins_idx if ins_idx is not None else len(lines)
        ingredients_lines = lines[start:end]
        if ins_idx is not None:
            instruction_lines = lines[ins_idx + 1 :]
    elif ins_idx is not None:
        instruction_lines = lines[ins_idx + 1 :]
        # Attempt to find ingredients above instructions.
        ingredients_lines = lines[1:ins_idx]

    # Parse ingredients candidates.
    parsed_ingredients: list[dict] = []
    # Method steps that ended up inside the ingredients block because their
    # heading was missing or unrecognised. Recovered as instructions rather than
    # imported as bogus ingredients.
    stray_instruction_lines: list[str] = []
    heading_words = {
        re.sub(r"[^a-z]", "", word) for word in _INGREDIENTS_HEADINGS + _INSTRUCTIONS_HEADINGS
    }
    for line in ingredients_lines:
        # Drop obvious non-ingredient headings.
        normalized = re.sub(r"[^a-z]", "", line.lower())
        if normalized in heading_words:
            continue
        tokens = line.split(" ")

        if tokens and _STEP_MARKER_RE.match(tokens[0]):
            stray_instruction_lines.append(" ".join(tokens[1:]).strip() or line)
            continue

        if len(tokens) >= _PROSE_WORD_COUNT and _parse_quantity(tokens[0]) is None:
            stray_instruction_lines.append(line)
            continue

        quantity_tokens = 1
        qty = _parse_quantity(tokens[0]) if tokens else None
        # Consume the complete mixed fraction before inspecting the unit.
        if len(tokens) > 1 and _MIXED_FRACTION_RE.fullmatch(" ".join(tokens[:2])):
            qty = _parse_quantity(" ".join(tokens[:2]))
            quantity_tokens = 2
        unit = None
        name_start = 0
        if qty is not None:
            name_start = quantity_tokens
            if len(tokens) > quantity_tokens:
                unit_token = tokens[quantity_tokens].rstrip(".").lower()
                # Recognised cooking measures outside the vocabulary are retained
                # for review; arbitrary ingredient words are never guessed as units.
                unit = to_recipe_unit(unit_token) or _UNIT_ALIASES.get(unit_token)
                if unit:
                    name_start = quantity_tokens + 1
        name = " ".join(tokens[name_start:]).strip()
        if not name:
            continue
        # Heuristic: ignore very short lines without a quantity.
        if qty is None and len(name) < 3:
            continue
        optional = bool(re.search(r"(?:,?\s*to taste|\s*\(?optional\)?)\s*$", name, re.I))
        if optional:
            name = re.sub(r"(?:,?\s*to taste|[,\s]*\(?optional\)?)\s*$", "", name, flags=re.I).strip()
        if not name:
            continue
        parsed_ingredients.append(
            {
                "raw_line": line,
                "name": name[:200],
                "quantity": qty if qty is not None else 1.0,
                "unit": unit or "each",
                "optional": optional,
            }
        )

    # Steps recovered from the ingredients block come first: they physically
    # preceded anything under a real instructions heading.
    instructions = "\n".join(stray_instruction_lines + instruction_lines).strip()

    # Fallback: if no instructions section was found, keep remaining lines as instructions.
    if not instructions and (ing_idx is not None and ingredients_lines):
        tail_start = (ins_idx + 1) if ins_idx is not None else (ing_idx + 1 + len(ingredients_lines))
        if tail_start < len(lines):
            instructions = "\n".join(lines[tail_start:]).strip()

    return {
        "title": title,
        "ingredients": parsed_ingredients,
        "instructions": instructions,
        "raw_text": raw_text,
    }

