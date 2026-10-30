"""The relay loop: queue new events, then deliver what is due (ADR 0026 §4).

Runs in the background for the life of the app. Off the request path, so a
slow or absent Wildstone only delays events — it never slows a write. Does
nothing at all until Wildstone is connected.
"""

from __future__ import annotations

import asyncio
import logging

from app.db.session import SessionLocal
from modules.wildstone.services.connection import ensure_started, is_configured
from modules.wildstone.services.delivery import deliver_pending
from modules.wildstone.services.relay import run_pass

logger = logging.getLogger(__name__)

INTERVAL_SECONDS = 10


def tick() -> None:
    with SessionLocal() as db:
        if not is_configured(db):
            return
        ensure_started(db)
        run_pass(db)
        deliver_pending(db)


async def relay_loop() -> None:
    while True:
        try:
            await asyncio.to_thread(tick)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - a failed tick must never crash the app
            logger.warning("wildstone relay tick failed", exc_info=True)
        await asyncio.sleep(INTERVAL_SECONDS)
