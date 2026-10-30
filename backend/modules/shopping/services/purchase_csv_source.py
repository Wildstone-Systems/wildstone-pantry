"""Bounded CSV source parsing without silently manufacturing positive amounts."""

import csv
import io
from decimal import Decimal, InvalidOperation

FIELDS = {
    "name": ("name", "item", "product", "description"),
    "quantity": ("quantity", "qty"),
    "unit": ("unit", "uom"),
    "barcode": ("barcode", "upc", "ean"),
}


def parse_source(csv_text, *, default_unit="each"):
    if len(csv_text) > 64000:
        raise ValueError("CSV exceeds64KiB; split the import")
    try:
        dialect = csv.Sniffer().sniff(csv_text[:4096], delimiters=",\t;|")
    except csv.Error:
        dialect = csv.excel
    reader = csv.reader(io.StringIO(csv_text), dialect=dialect, strict=True)
    physical = csv_text.splitlines(keepends=True)
    records = []
    previous = 0
    try:
        for cells in reader:
            raw = "".join(physical[previous : reader.line_num]).rstrip("\r\n")
            previous = reader.line_num
            if any(cell.strip() for cell in cells):
                if len(raw) > 4000:
                    raise ValueError("CSV row exceeds4000characters")
                records.append((raw, [cell.strip() for cell in cells]))
            if len(records) > 201:
                raise ValueError("CSV exceeds200data rows")
    except csv.Error as exc:
        raise ValueError("CSV quoting is malformed; correct it before preview") from exc
    if not records:
        raise ValueError("CSV has no nonempty data rows")
    normalized = [
        "".join(ch for ch in c.lower() if ch.isalnum() or ch in "_ ").replace(" ", "_") for c in records[0][1]
    ]
    header = any(c in tuple(alias for aliases in FIELDS.values() for alias in aliases) for c in normalized)
    if header:
        if len(normalized) != len(set(normalized)):
            raise ValueError("CSV has duplicate header names")
        records = records[1:]
    if len(records) > 200:
        raise ValueError("CSV exceeds200data rows")
    result = []
    for raw, cells in records:
        if header:
            data = dict(zip(normalized, cells))

            def value(key):
                return next((data[k] for k in FIELDS[key] if data.get(k)), "")

            name, quantity, unit, barcode = (value(k) for k in ("name", "quantity", "unit", "barcode"))
        else:
            name, quantity, unit, barcode = (cells + [""] * 4)[:4]
        if not name:
            name = ""
        unit = unit or default_unit
        warnings = []
        valid = False
        nonstock = False
        try:
            amount = Decimal(quantity)
            valid = amount.is_finite() and amount > 0
            nonstock = amount.is_finite() and amount <= 0
        except InvalidOperation:
            pass
        if not quantity:
            warnings.append("Missing quantity: enter and verify the actual amount")
        elif nonstock:
            warnings.append("Return/nonpositive source amount: exclude this row")
        elif not valid:
            warnings.append("Invalid quantity: correct and verify the actual amount")
        if not name:
            warnings.append("Missing product name")
        if len(name) > 200:
            warnings.append("Long product name shortened for editing; verify raw source")
        if len(quantity) > 40 or len(unit) > 16 or len(barcode) > 64:
            raise ValueError("CSV amount, unit or barcode exceeds supported length")
        result.append(
            {
                "raw_line": raw,
                "name": name[:200],
                "quantity": quantity,
                "unit": unit,
                "barcode": barcode or None,
                "source_quantity": quantity,
                "source_nonstock": nonstock,
                "role": "return" if nonstock else "stock" if valid and name else "unknown",
                "warnings": warnings,
            }
        )
    if not result:
        raise ValueError("CSV has no data rows")
    return result
