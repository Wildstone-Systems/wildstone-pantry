#!/usr/bin/env python3
"""Assert the app refuses to start in a protected env with insecure defaults.

A positive control for the config guard in ``app.core.config``: instantiating
Settings under ``staging`` with development defaults MUST raise.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ["ENVIRONMENT"] = "staging"
# Ensure insecure defaults are in play (clear any overrides).
for key in ("DATABASE_URL", "AUTH_PERSONAL_PASSWORD"):
    os.environ.pop(key, None)

from pydantic import ValidationError  # noqa: E402

try:
    # Importing instantiates module-level Settings(); under 'staging' with
    # insecure defaults this MUST raise.
    from app.core.config import Settings  # noqa: F401
except ValidationError:
    print("protected-env guard OK: insecure defaults rejected under 'staging'")
    sys.exit(0)

print("FAIL: Settings accepted insecure defaults under 'staging'")
sys.exit(1)
