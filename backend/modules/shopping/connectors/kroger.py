"""Kroger Cart target (OUT) — DORMANT scaffold (step 5a; goes live in 5c).

Kroger's Cart API is the one self-serve, indie-obtainable vendor OUT path
(add-to-cart via OAuth). It stays
dormant until ``KROGER_CLIENT_ID`` / ``KROGER_CLIENT_SECRET`` are configured;
the OAuth + Cart push itself lands in step 5c once a developer key exists.
"""

from __future__ import annotations

from app.core import settings
from modules.shopping.connectors.base import ExportLine, ExportResult, NotConfiguredError


class KrogerCartTarget:
    key = "kroger"
    label = "Send to Kroger cart"

    def configured(self) -> bool:
        return bool(settings.kroger_client_id.strip() and settings.kroger_client_secret.strip())

    def export(self, lines: list[ExportLine]) -> ExportResult:  # noqa: ARG002 (lines used in 5c)
        if not self.configured():
            raise NotConfiguredError(
                "Kroger is not configured — set KROGER_CLIENT_ID and KROGER_CLIENT_SECRET to enable."
            )
        # Step 5c: OAuth2 auth-code flow (scope cart.basic:write) + add-to-cart.
        raise NotImplementedError("Kroger cart push is implemented in step 5c.")
