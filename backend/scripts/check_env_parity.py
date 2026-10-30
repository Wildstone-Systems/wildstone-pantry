#!/usr/bin/env python3
"""Fail if Settings fields and .env.example keys are out of sync.

Every configurable field in ``app.core.config.Settings`` must have a documented
key in the repo-root ``.env.example`` (and vice versa).
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = REPO_ROOT / "backend"
sys.path.insert(0, str(BACKEND_ROOT))

from app.core.config import Settings  # noqa: E402


def _env_example_keys() -> set[str]:
    keys: set[str] = set()
    for line in (REPO_ROOT / ".env.example").read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        keys.add(line.split("=", 1)[0].strip().upper())
    return keys


def main() -> int:
    setting_keys = {name.upper() for name in Settings.model_fields}
    example_keys = _env_example_keys()

    missing_in_example = setting_keys - example_keys
    unknown_in_example = example_keys - setting_keys

    ok = True
    if missing_in_example:
        ok = False
        print("Settings fields missing from .env.example:")
        for key in sorted(missing_in_example):
            print(f"  - {key}")
    if unknown_in_example:
        ok = False
        print(".env.example keys not present in Settings:")
        for key in sorted(unknown_in_example):
            print(f"  - {key}")

    if ok:
        print(f"env parity OK ({len(setting_keys)} keys)")
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
