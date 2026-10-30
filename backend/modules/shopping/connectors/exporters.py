"""Credential-free shopping-list export targets (step 5a).

Always available — no vendor account needed. Copy the list into any notes app or
a store's own list, or import the CSV.
"""

from __future__ import annotations

import csv
import io

from modules.shopping.connectors.base import ExportLine, ExportResult


def _fmt_qty(value) -> str:
    # Trim trailing zeros: 2.000 -> 2, 1.500 -> 1.5
    text = f"{value}"
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


class TextExportTarget:
    key = "text"
    label = "Copy as text"

    def configured(self) -> bool:
        return True

    def export(self, lines: list[ExportLine]) -> ExportResult:
        if lines:
            body = "\n".join(f"- {_fmt_qty(line.quantity)} {line.unit} {line.name}" for line in lines)
        else:
            body = "(nothing to buy)"
        return ExportResult(target=self.key, format="text", content_type="text/plain", body=body)


class CsvExportTarget:
    key = "csv"
    label = "Download CSV"

    def configured(self) -> bool:
        return True

    def export(self, lines: list[ExportLine]) -> ExportResult:
        buffer = io.StringIO()
        writer = csv.writer(buffer)
        writer.writerow(["name", "quantity", "unit"])
        for line in lines:
            writer.writerow([line.name, _fmt_qty(line.quantity), line.unit])
        return ExportResult(
            target=self.key,
            format="csv",
            content_type="text/csv",
            body=buffer.getvalue(),
            filename="shopping-list.csv",
        )
