"""Normalize explicit nutrition edits without accepting client verification claims."""
from decimal import ROUND_HALF_UP, Decimal

MACROS = {
    "energy-kcal_100g": "energy_kcal_100g", "proteins_100g": "protein_g_100g",
    "fat_100g": "fat_g_100g", "carbohydrates_100g": "carbs_g_100g",
    "fiber_100g": "fiber_g_100g", "sugars_100g": "sugars_g_100g",
    "saturated-fat_100g": "saturated_fat_g_100g", "sodium_100g": "sodium_g_100g",
}


def _number(value, column: str) -> Decimal | None:
    if value is None:
        return None
    try:
        result = Decimal(str(value))
    except (ValueError, ArithmeticError) as exc:
        raise ValueError(f"{column} must be numeric") from exc
    if not result.is_finite() or result < 0:
        raise ValueError(f"{column} must be a finite non-negative number")
    return result


def _same_stored(value: Decimal | None, old: Decimal | None) -> bool:
    if value is None or old is None:
        return value is old
    # Every managed column is Numeric(10, 2). JSON retains reference precision;
    # an editor echo of the rounded column must not replace that source value.
    try:
        return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP) == old
    except ArithmeticError:
        return False


def explicit_nutrition(data: dict, fields: set[str], item=None) -> None:
    """Preserve untouched reference precision; synchronize genuine manual edits.

    Structured inputs compare at stored precision. Explicit JSON edits compare
    at full precision, so changing .003 to .004 remains a real manual edit even
    though both structured values round to zero. Omitted columns never rewrite
    their reference JSON. Verification metadata is always server-owned.
    """
    has_json = "nutrition" in fields
    if not (fields & ({"nutrition"} | set(MACROS.values()))):
        return
    previous = dict(item.nutrition or {}) if item is not None else {}
    raw = dict(data.get("nutrition") or {}) if has_json else dict(previous)
    raw.pop("_reference_verification", None)
    changed = False
    for key, column in MACROS.items():
        old = getattr(item, column, None) if item is not None else None
        if column in fields:
            value = _number(data[column], column)
            if item is not None and _same_stored(value, old):
                # Do not let a database dialect re-round a sub-precision echo.
                data[column] = old
                if has_json:
                    # An unchanged column echo permits a compatible explicit
                    # fine-precision JSON edit. Conflicting columns take priority.
                    raw_value = _number(raw.get(key), column)
                    if key in raw and (
                        _same_stored(raw_value, old) or raw.get(key) == previous.get(key)
                    ):
                        if raw_value is None:
                            raw.pop(key, None)
                        else:
                            raw[key] = float(raw_value)
                    elif key in previous:
                        raw[key] = previous[key]
                    else:
                        raw.pop(key, None)
                continue
            changed = changed or not _same_stored(value, old)
        elif has_json:
            value = _number(raw.get(key), column)
            if item is not None and raw.get(key) == previous.get(key) and (key in previous or old is None):
                continue  # unchanged JSON retains its source precision and stored column
            changed = changed or raw.get(key) != previous.get(key) or not _same_stored(value, old)
        else:
            continue
        data[column] = value
        if value is None:
            raw.pop(key, None)
        else:
            raw[key] = float(value)
    changed = changed or {k: v for k, v in raw.items() if k in MACROS} != {
        k: v for k, v in previous.items() if k in MACROS
    }
    if changed:
        raw["_reference_verification"] = {
            "status": "manual", "source": "manual", "why": "Human-entered nutrition",
        }
        data["nutrition_estimated"] = False
        data["nutrition_source"] = "Manual entry"
    elif "_reference_verification" in previous:
        raw["_reference_verification"] = previous["_reference_verification"]
    data["nutrition"] = raw or None
