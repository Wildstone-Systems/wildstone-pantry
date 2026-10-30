from __future__ import annotations

import re
from decimal import Decimal

_UNIT_MAP = {
    "ea": "each",
    "each": "each",
    "lb": "lb",
    "oz": "oz",
    "g": "g",
    "kg": "kg",
    "ml": "ml",
    "l": "l",
}

_PRICE_SUFFIX_RE = re.compile(r"\s+\$?\d+(?:\.\d{2})?$")
_NON_STOCK_RE = re.compile(
    r"^(?:sub\s*total|grand\s+total|total|sales\s+tax|tax|coupon|discount|"
    r"savings|balance\s+due|amount\s+due|change|cash|credit|debit)"
    r"(?:\s*[:,]\s*|\s+)[+-]?\$?\d+(?:\.\d+)?\s*$",
    flags=re.IGNORECASE,
)
_MAIN_RE = re.compile(
    r"^(?P<name>[A-Za-z][A-Za-z0-9\-\s'&]+?)"
    r"(?:\s+(?P<qty>\d+(?:\.\d+)?)(?:\s*(?P<quantity_unit>ea|each|lb|oz|g|kg|ml|l))?)?"
    r"(?:\s+(?P<unit>ea|each|lb|oz|g|kg|ml|l))?$",
    flags=re.IGNORECASE,
)


def _normalize_unit(raw: str | None) -> str:
    if not raw:
        return "each"
    return _UNIT_MAP.get(raw.strip().lower(), "each")


def _parse_line(line: str) -> dict | None:
    normalized = line.strip()
    if not normalized or _NON_STOCK_RE.match(normalized):
        return None

    if normalized.count(",") >= 1:
        parts = [p.strip() for p in normalized.split(",") if p.strip()]
        if len(parts) >= 2:
            name = parts[0]
            qty_raw = parts[1]
            unit_raw = parts[2] if len(parts) >= 3 else "each"
            try:
                qty = Decimal(qty_raw)
            except Exception:
                qty = Decimal("1")
            return {"raw_line": line, "name": name[:200], "quantity": qty, "unit": _normalize_unit(unit_raw)}

    clean = _PRICE_SUFFIX_RE.sub("", normalized)
    match = _MAIN_RE.match(clean)
    if not match:
        return None

    name = (match.group("name") or "").strip()
    if len(name) < 2:
        return None

    qty_raw = match.group("qty")
    unit_raw = match.group("quantity_unit") or match.group("unit")

    qty = Decimal(qty_raw) if qty_raw else Decimal("1")
    if qty <= 0:
        qty = Decimal("1")

    return {
        "raw_line": line,
        "name": name[:200],
        "quantity": qty,
        "unit": _normalize_unit(unit_raw),
    }


def parse_receipt_text(raw_text: str) -> list[dict]:
    parsed: list[dict] = []
    for line in raw_text.splitlines():
        out = _parse_line(line)
        if out:
            parsed.append(out)
    return parsed
