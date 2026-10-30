import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.openapi.docs import get_swagger_ui_html
from fastapi.middleware.cors import CORSMiddleware

import modules  # noqa: F401  (imports module manifests so they self-register)
from app.core import settings
from app.routes import (
    activation_router,
    agent_guide_router,
    agent_keys_router,
    auth_router,
    devices_router,
    entitlement_router,
    feedback_router,
    health_router,
    household_router,
    oidc_router,
    setup_router,
    system_router,
)
from core.modules import mount_modules, start_background_tasks
from handlers.registration import register_default_handlers


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: wire event handlers and top up the ingredient reference seed
    # (density / grams-per-each) so unit conversion works out of the box for
    # common foods (ADR 0016 Phase 2). Idempotent — only inserts missing rows.
    register_default_handlers()
    _warn_deprecated_license_settings()
    _seed_ingredient_reference()
    backup_task = _start_backup_scheduler()
    refresh_task = _start_entitlement_refresher()
    module_tasks = start_background_tasks()
    yield
    for task in (backup_task, refresh_task, *module_tasks):
        if task is not None:
            task.cancel()


def _start_backup_scheduler():
    """Nightly-ish backups (ADR 0018 §6): hourly tick, runs when the newest
    backup is >~24h old (so a fresh boot with no backups takes one right away).
    No-op unless BACKUP_DIR is set."""
    import asyncio
    from datetime import datetime, timezone

    from app.services import backup as backup_service

    if not backup_service.backup_enabled():
        return None

    async def _loop() -> None:
        while True:
            try:
                if backup_service.should_run(datetime.now(timezone.utc)):
                    info = await asyncio.to_thread(backup_service.run_backup)
                    logging.getLogger(__name__).info("backup written: %s (%s bytes)", info.name, info.size_bytes)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - a failed backup must never crash the app
                logging.getLogger(__name__).warning("scheduled backup failed", exc_info=True)
            await asyncio.sleep(3600)

    return asyncio.get_running_loop().create_task(_loop())


def _start_entitlement_refresher():
    """Daily entitlement renewal (ADR 0018 §2): jittered hourly tick; refreshes
    when the token is >24h since its last renewal. Free mode (no
    LICENSE_SERVER_URL) starts nothing — zero network calls."""
    import asyncio
    import random

    from app.services.entitlement import MODE_CONNECTED, deployment_mode

    if deployment_mode() != MODE_CONNECTED:
        return None

    async def _loop() -> None:
        from app.db.session import SessionLocal
        from app.services import activation as activation_service

        while True:
            try:
                def _tick() -> None:
                    with SessionLocal() as db:
                        state = activation_service.get_state(db)
                        if state is not None and activation_service.refresh_due(state):
                            activation_service.refresh(db)

                await asyncio.to_thread(_tick)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - refresh failures never crash the app
                logging.getLogger(__name__).warning("entitlement refresh tick failed", exc_info=True)
            await asyncio.sleep(3600 + random.randint(0, 600))

    return asyncio.get_running_loop().create_task(_loop())


def _warn_deprecated_license_settings() -> None:
    # ADR 0018: the shared-password license gate is gone. Activation keys (the
    # only remaining meaning of "license") never gate local features. Warn any
    # deploy still carrying the dead knobs so they get cleaned out of env files.
    import os

    if os.getenv("LICENSE_KEY", "").strip() or os.getenv("ENFORCE_LICENSE", "").strip().lower() in {"1", "true", "yes"}:
        logging.getLogger(__name__).warning(
            "LICENSE_KEY / ENFORCE_LICENSE are deprecated and ignored (ADR 0018). "
            "The app no longer gates any route on a shared license key; use "
            "AUTH_ENABLED for access control. Remove these from your environment."
        )


def _seed_ingredient_reference() -> None:
    from app.db.session import SessionLocal
    from modules.catalog.services.ingredient_reference import seed_reference

    try:
        db = SessionLocal()
        try:
            seed_reference(db)
        finally:
            db.close()
    except Exception:  # noqa: BLE001 - seeding is best-effort; never block boot
        logging.getLogger(__name__).warning("ingredient reference seed skipped", exc_info=True)


def create_app() -> FastAPI:
    app = FastAPI(title="PantryOS API", version=settings.app_version, lifespan=lifespan)

    # Serve the schema and docs under /api as well as at the root.
    #
    # Every deployment puts the frontend at "/" and proxies only "/api" to the
    # backend, so a bare /openapi.json request from outside returns the Vite HTML
    # shell — an agent asking "what endpoints exist?" gets a web page, parses no
    # endpoints, and concludes the API doesn't have them. (That is exactly what
    # happened: an agent reported PantryOS had no meal-logging API while
    # /api/consumption/plate was live.) Mounting the same schema under the
    # already-proxied prefix makes discovery work through any such deployment
    # without touching proxy config. The root paths keep working unchanged.
    @app.get("/api/openapi.json", include_in_schema=False)
    def _openapi_via_api_prefix() -> dict:
        return app.openapi()

    @app.get("/api/docs", include_in_schema=False)
    def _docs_via_api_prefix():
        return get_swagger_ui_html(openapi_url="/api/openapi.json", title="PantryOS API")

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_allow_origin_list(),
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # /api/v1 alias (ADR 0018 §8): native clients pin the versioned prefix;
    # a path rewrite keeps one implementation and one OpenAPI schema. /api
    # remains as the browser SPA's (deprecated-for-clients) spelling.
    @app.middleware("http")
    async def _api_v1_alias(request, call_next):  # noqa: ANN001, ANN202
        path = request.scope.get("path", "")
        if path == "/api/v1" or path.startswith("/api/v1/"):
            request.scope["path"] = "/api" + path[len("/api/v1"):]
        return await call_next(request)

    app.include_router(health_router)
    app.include_router(auth_router, prefix="/api")
    # Native OIDC SSO (ADR 0020): public login/callback/status routes beside auth.
    app.include_router(oidc_router, prefix="/api")
    # First-run wizard: open while setup is incomplete, 403 after (ADR 0018 §7).
    app.include_router(setup_router, prefix="/api")
    # Entitlement/mode is ungated — the frontend reads it up front to decide
    # which premium surfaces to show (ADR 0012/0018).
    app.include_router(entitlement_router, prefix="/api")
    # Agent API guide — ungated so an agent can discover how to call the API.
    app.include_router(agent_guide_router, prefix="/api")
    # Feature modules (catalog, ...). Local features are never license-gated
    # (ADR 0018); per-route auth/entitlement dependencies live on the routes.
    mount_modules(app, api_prefix="/api")
    app.include_router(feedback_router, prefix="/api")
    app.include_router(agent_keys_router, prefix="/api")
    # Household profiles: reads open (kiosk), writes admin-gated per-route (ADR 0004).
    app.include_router(household_router, prefix="/api")
    # System surface: /version open like /health; backup management admin-only.
    app.include_router(system_router, prefix="/api")
    # Activation key management (ADR 0018 §2): real-admin-only.
    app.include_router(activation_router, prefix="/api")
    # Device pairing + management (ADR 0018 §8).
    app.include_router(devices_router, prefix="/api")

    _mount_spa(app)

    return app


def _mount_spa(app: FastAPI) -> None:
    """Product image: serve the built frontend from this process (ADR 0018 §4).

    Mounted LAST so every API route wins; the mount only catches what nothing
    else matched. Dev (FRONTEND_DIST unset) mounts nothing — Vite serves the UI.
    """
    from pathlib import Path

    dist = settings.frontend_dist.strip()
    if not dist:
        return
    dist_path = Path(dist)
    if not (dist_path / "index.html").is_file():
        logging.getLogger(__name__).warning(
            "FRONTEND_DIST=%s has no index.html; not serving the SPA from the backend.", dist
        )
        return

    from app.core.spa import SpaStaticFiles

    app.mount("/", SpaStaticFiles(directory=dist_path, html=True), name="spa")


app = create_app()
