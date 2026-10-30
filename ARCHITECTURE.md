# PantryOS Architecture

PantryOS is a **feature-sliced modular monolith**: one FastAPI backend + one
React frontend, organized by domain module rather than by technical layer.

## Backend

```
backend/
  core/                 # platform kernel (no domain logic)
    events/             # tiny synchronous in-process event bus (no broker)
    modules/            # ModuleManifest + registry (mount_modules)
  app/
    core/               # shared infra: config, security, db_types, ...
    db/                 # engine + session (SQLite-aware for tests)
    main.py             # app factory + lifespan; mounts module routers
  modules/<domain>/     # feature modules
  migrations/           # Alembic (revision id < 32 chars, linear graph)
  tests/                # cross-module integration tests (in-process TestClient)
  conftest.py           # SQLite test harness
```

Target domain modules (migration in progress, ADR 0001 Phase 2):
**catalog · inventory · shopping · planning · tasks · automation**.

### Rules
- **Routes → services → repository → ORM.** No direct DB access from routes.
- Modules register a `ModuleManifest`; the app factory mounts them — adding a
  module never edits `app/main.py`.
- Cross-module reactions go through the event bus or an explicit service
  interface, never by reaching into another module's internals.
- Shared infra lives in `app/core` / `core`; it is imported, never copied.

### Configuration & safety
- `pydantic-settings` (`app/core/config.py`). In `staging`/`production` the app
  **refuses to start on insecure defaults**.
- Only `.env.example` is committed; CI checks `.env.example` ↔ Settings parity.

### Testing
- In-process `TestClient` against an isolated SQLite DB (`conftest.py`), schema
  rebuilt per test. No live server, no DB mocking. Portable column types
  (`app/core/db_types`) keep Postgres-only types (`UUID`/`JSONB`) SQLite-safe.

## Frontend

```
frontend/src/
  modules/<domain>/     # feature folders: pages/, hooks/, (components/)
  api/                  # axios client + typed React Query hooks
  routes/               # central route config (lazy-loaded module pages)
  theme/                # Mantine theme tokens
```
Server state via React Query; a single axios client; Mantine v7 UI.

## CI
`.github/workflows/ci.yml` — backend (config guards, in-process tests, ruff/mypy/
bandit non-blocking) and frontend (lint, vitest, build).
