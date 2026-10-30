"""Regenerate the committed API-surface snapshot (ADR 0018 §8).

The snapshot is the list of "METHOD /path" lines the app serves. CI diffs the
live app against it, so removing or renaming a route is a deliberate,
reviewed event — native clients pin /api/v1 and must not break silently.

    python scripts/generate_api_surface.py   # rewrites tests/api_surface.txt
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def surface_lines() -> list[str]:
    from app.main import app

    lines: set[str] = set()
    for route in app.routes:
        methods = getattr(route, "methods", None) or set()
        path = getattr(route, "path", "")
        if not path.startswith("/api") and path != "/health":
            continue
        for method in methods - {"HEAD", "OPTIONS"}:
            lines.add(f"{method} {path}")
    return sorted(lines)


if __name__ == "__main__":
    target = Path(__file__).resolve().parents[1] / "tests" / "api_surface.txt"
    target.write_text("\n".join(surface_lines()) + "\n")
    print(f"wrote {len(surface_lines())} routes to {target}")
