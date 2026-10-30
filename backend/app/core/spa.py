"""Serve the built SPA from the backend (ADR 0018 §4 — single origin).

In the product image the frontend's ``dist/`` is baked in and served by the
API process itself: one port, no CORS, no separate web server. Client-side
routes (e.g. ``/shopping-list``) fall back to ``index.html``; anything under
``/api`` that reached the mount is a real 404 and must never turn into HTML.

Dev is unchanged: with ``FRONTEND_DIST`` unset (the default), nothing mounts
and the Vite dev server keeps serving the UI on its own port.
"""

from __future__ import annotations

from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.staticfiles import StaticFiles

# Path prefixes that must keep their native 404s instead of the SPA fallback.
_NON_SPA_PREFIXES = ("api/", "health", "docs", "openapi.json", "redoc")


class SpaStaticFiles(StaticFiles):
    async def get_response(self, path: str, scope):
        # Starlette normalizes to OS filesystem separators before this method.
        # Match URL prefixes portably without changing the static-file lookup.
        non_spa_path = path.replace("\\", "/")
        try:
            response = await super().get_response(path, scope)
        except StarletteHTTPException as exc:
            if exc.status_code == 404 and not non_spa_path.startswith(_NON_SPA_PREFIXES):
                return await super().get_response("index.html", scope)
            raise
        if response.status_code == 404 and not non_spa_path.startswith(_NON_SPA_PREFIXES):
            return await super().get_response("index.html", scope)
        return response
