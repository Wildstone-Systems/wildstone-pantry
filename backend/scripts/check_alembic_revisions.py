#!/usr/bin/env python3
"""Fail if the Alembic graph is unhealthy.

Two checks:

1. Every revision id is < 32 chars — ``alembic_version.version_num`` is
   ``varchar(32)``; keep detail in the filename and the id short.
2. The graph has exactly one head. Two branches authored against the same
   parent merge cleanly in git (different files, no textual conflict) but leave
   Alembic with two heads, and ``alembic upgrade head`` then refuses to run —
   which only shows up when something actually deploys, since the test harness
   builds its schema with ``create_all`` and never runs a migration.
   AGENTS.md requires a linear graph.
"""

from __future__ import annotations

import re
from pathlib import Path

VERSIONS = Path(__file__).resolve().parents[1] / "migrations" / "versions"
_REVISION = re.compile(r"^revision\s*(?::[^=]+)?=\s*['\"]([^'\"]+)['\"]", re.MULTILINE)
_DOWN_REVISION = re.compile(
    r"^down_revision\s*(?::[^=]+)?=\s*(?:['\"]([^'\"]+)['\"]|None)", re.MULTILINE
)


def main() -> int:
    too_long: list[tuple[str, str]] = []
    revisions: set[str] = set()
    parents: dict[str, str | None] = {}
    file_by_revision: dict[str, str] = {}
    count = 0

    for path in sorted(VERSIONS.glob("*.py")):
        text = path.read_text()
        match = _REVISION.search(text)
        if not match:
            continue
        count += 1
        rev = match.group(1)
        revisions.add(rev)
        file_by_revision[rev] = path.name
        if len(rev) >= 32:
            too_long.append((path.name, rev))

        down_match = _DOWN_REVISION.search(text)
        parents[rev] = down_match.group(1) if down_match else None

    ok = True

    if too_long:
        ok = False
        print("Alembic revision ids must be < 32 chars:")
        for name, rev in too_long:
            print(f"  - {name}: {rev} ({len(rev)} chars)")

    # A head is a revision nothing else points back to.
    referenced = {parent for parent in parents.values() if parent}
    heads = sorted(revisions - referenced)
    if len(heads) > 1:
        ok = False
        print("Alembic has multiple heads — `alembic upgrade head` will refuse to run:")
        for head in heads:
            print(f"  - {head} ({file_by_revision.get(head, '?')})")
        print("Re-parent the newer branch onto the other so the graph stays linear.")

    missing = sorted(
        parent for parent in referenced if parent not in revisions
    )
    if missing:
        ok = False
        print("Alembic down_revision points at a revision that does not exist:")
        for parent in missing:
            print(f"  - {parent}")

    if not ok:
        return 1

    print(f"alembic revision graph OK ({count} migrations, single head: {heads[0] if heads else 'none'})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
