"""API contract freeze + /api/v1 alias (ADR 0018 §8).

The committed surface snapshot makes breaking the API a deliberate, reviewed
event: run ``python scripts/generate_api_surface.py`` after intentional
changes. The /api/v1 alias is what native clients pin.
"""

from __future__ import annotations

import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.generate_api_surface import surface_lines  # noqa: E402

SNAPSHOT = Path(__file__).parent / "api_surface.txt"


def test_api_surface_matches_snapshot() -> None:
    committed = set(SNAPSHOT.read_text().splitlines())
    live = set(surface_lines())
    removed = committed - live
    added = live - committed
    assert not removed and not added, (
        "API surface changed. Removed: "
        f"{sorted(removed)}; added: {sorted(added)}. If intentional, run "
        "`python scripts/generate_api_surface.py` and commit the snapshot "
        "(removals/renames break pinned native clients — ADR 0018 §8)."
    )


def test_api_v1_alias_serves_the_same_api(client: httpx.Client) -> None:
    plain = client.get("/api/entitlement")
    versioned = client.get("/api/v1/entitlement")
    assert versioned.status_code == 200
    assert versioned.json() == plain.json()
    # Misses under the alias stay JSON 404s too.
    assert client.get("/api/v1/definitely-not-a-route").status_code == 404
