# PantryOS

A next-generation household operations platform (a modern take on Grocy):
inventory, shopping, meal planning, tasks, and an agentic automation layer.
Built as a feature-sliced **modular monolith** (see `ARCHITECTURE.md`):
- PostgreSQL (SQLite in the test harness)
- FastAPI (Python) + SQLAlchemy + Alembic
- Vite + React frontend (PWA plugin enabled)

## Project Layout

```text
/backend
  /core              # platform kernel: event bus, module registry/manifests
  /app
    /core            # shared infra: config, security, db_types
    /db
    /models          # shared ORM registry (per-module ownership documented in _meta)
    /routes /services /schemas   # platform layer: auth, entitlement, feedback, health
    main.py          # app factory + lifespan; mounts module routers
  /modules           # feature modules: catalog, inventory, shopping, planning, automation
    /<domain>/{api,services,schemas,manifest.py,_meta.py,README.md}
  /migrations
  /scripts           # CI guards (env parity, alembic, protected-env)
  conftest.py        # in-process SQLite test harness
/frontend
  /src/modules/<domain>/{pages,hooks}   # feature-sliced UI
  /src/{api,routes,theme,components}
/scripts
/docker
/docs                # SSO / OIDC setup guide
.github/workflows    # CI
.env.example  AGENTS.md  ARCHITECTURE.md  README.md
```

> Architecture overview: [`ARCHITECTURE.md`](ARCHITECTURE.md).

## API Contracts (Phase 0 + Phase 1 + Phase 2 + Phase 3 + Phase 4 + Phase 5)

- `GET /health`
  - Response:
  - `{ "status": "ok", "database": "connected" }`
- `GET /api/items`
- `POST /api/items`
- `GET /api/items/{item_id}`
- `PUT /api/items/{item_id}`
- `DELETE /api/items/{item_id}`
- `GET /api/locations`
- `POST /api/locations`
- `GET /api/inventory?location_id={uuid}`
- `POST /api/inventory/add`
- `POST /api/inventory/remove`
- `POST /api/inventory/adjust`
- `POST /api/inventory/move`
- `GET /api/inventory/transactions?location_id={uuid}&limit={n}`
- `GET /api/barcodes/lookup/{barcode}`
- `POST /api/barcodes/alias`
- `POST /api/receipts/ingest`
- `POST /api/receipts/ingest-image`
- `GET /api/recipes`
- `POST /api/recipes`
- `GET /api/recipes/{recipe_id}`
- `PUT /api/recipes/{recipe_id}/ingredients`
- `POST /api/recipes/{recipe_id}/cook`
- `GET /api/recipes/suggestions/{location_id}`
- `GET /api/insights/trends/{location_id}`
- `GET /api/insights/restock/{location_id}`
- `GET /api/insights/expiring/{location_id}`
- `POST /api/auth/bootstrap-admin`
- `POST /api/auth/login`
- `GET /api/auth/me`
- `GET /api/auth/users` (admin)
- `GET /api/entitlement`
- `POST /api/purchases/import-csv`
- `POST /api/purchases/preview-csv`
- `GET /api/purchases`
- `GET /api/purchases/{purchase_id}`

## Run With Docker

From repo root:

```bash
docker compose up --build
```

Services:
- Postgres: `localhost:5432` (loopback only — dev credentials are well-known)
- Backend API: `localhost:8000`
- Frontend: `localhost:5173`

Backend container applies migrations automatically at startup.

### Installing without a checkout (external testers)

Testers can install from the release bundle:

- [`release/`](release/) — the public-safe deployment bundle (image-only
  compose, `.env.example`, install guide), attached to every GitHub Release by
  the Release workflow.

### One-command install (Linux / NAS / Raspberry Pi)

Review the script before piping it to a shell:

```bash
curl -fsSL https://raw.githubusercontent.com/Wildstone-Systems/wildstone-pantry/main/scripts/install-product.sh | bash
```

Generates secrets into `~/.pantryos`, starts the product stack, and prints the
LAN address. Windows/macOS users get the desktop launcher instead (`launcher/`,
built by the Launcher workflow).

### Product stack (single origin)

The product image (`docker/Dockerfile`, published to
`ghcr.io/wildstone-systems/pantryos` by the Release workflow on `v*` tags) bakes the
built frontend into the backend, which serves UI + API on **one port** — no
CORS, no Vite, Postgres unpublished (ADR 0018). Run it locally:

```bash
POSTGRES_PASSWORD=$(openssl rand -hex 24) \
CONFIG_SECRET_KEY=$(python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())") \
docker compose -f docker-compose.product.yml up --build -d
# → http://localhost:8420
```

### Single sign-on (OIDC)

Self-hosters already running an identity provider (LLDAP + Authelia/authentik/
Keycloak/Pocket ID) can let their household log in with it instead of a
second PantryOS password — see
[`docs/sso-oidc-setup.md`](docs/sso-oidc-setup.md) for the walkthrough. Local
admin login always stays available as a break-glass path.

## Isolated Test Stack (Docker Compose)

This runs a fresh stack on alternate ports with a separate Postgres volume, then executes `pytest` in a container.

```bash
docker compose -f docker-compose.test.yml up --build --abort-on-container-exit --exit-code-from tests
```

Ports (test stack):
- Postgres: `localhost:15432`
- Backend API: `localhost:18000`
- Frontend: `localhost:15173`

## Staging Stack (Docker Compose)

A long-lived deployment (e.g. a household box on the LAN) on its own project
name, volume and ports, so it never collides with the dev or test stacks.

```bash
docker compose --env-file .env.staging -f docker-compose.staging.yml up --build -d
```

Ports (staging stack):
- Postgres: `127.0.0.1:25432` (loopback only — use an SSH tunnel to reach it)
- Backend API: `28000` (all interfaces)
- Frontend: `25173` (all interfaces)

Secrets come from an untracked `.env.staging` (copy `.env.example` and fill it
in). The compose file pins `ENVIRONMENT=staging` on the backend service itself
rather than trusting the env file, so the stack cannot be started in
development mode by accident. That makes the backend refuse to boot on insecure
defaults, so `DATABASE_URL` and `AUTH_PERSONAL_PASSWORD` must be
real values — see `_validate_protected_env` in `app/core/config.py`.

To reach the UI by hostname rather than IP, set `STAGING_ALLOWED_HOSTS` (feeds
Vite's `allowedHosts`); to let browser clients on other hosts call the API
cross-origin, set `CORS_ALLOW_ORIGINS`.

## Quick Install

```bash
./scripts/install.sh
```

## Local Backend Run (without Docker)

```bash
cd backend
python -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
cp ../.env.example .env
alembic upgrade head
uvicorn main:app --reload --host 0.0.0.0 --port 8000
```

## Seed Script

```bash
cd backend
python ../scripts/seed.py
```

Seeds default categories and inventory locations.

## Smoke Test

Prerequisite: API running on `http://localhost:8000` with real Postgres.

```bash
python scripts/smoke_test.py
```

Smoke flow:
1. Health check
2. Bootstrap admin
3. Login
4. Create item
5. Fetch item
6. Update item
7. Create locations
8. Add inventory
9. Move inventory
10. Verify inventory + transactions
11. Barcode lookup
12. Receipt text ingestion
13. Create recipe + ingredients
14. Cook recipe
15. Recipe suggestions
16. AI insight endpoints
17. Auth me endpoint
18. Entitlement check
19. Delete item and confirm deletion
20. Smoke passed

## QA Gate (Recommended)

One command to run a consistent QA protocol:
- Check open in-app feedback (triage queue)
- Frontend unit tests
- Frontend production build (TypeScript + Vite)
- Isolated integration tests via `docker-compose.test.yml`
- Smoke test against the dev stack (can be skipped)

```bash
./scripts/qa.sh
```

Useful environment variables:
- `QA_API_BASE_URL` (defaults to `http://localhost:8000`)
- `QA_SKIP_SMOKE=1` (skip smoke test)

To just list feedback items:

```bash
python scripts/check_feedback.py --status open
```

## Minimal Integration Tests

With API running:

```bash
cd backend
pytest -q tests/test_phase0_api.py tests/test_phase1_inventory.py
pytest -q tests/test_phase2_ingestion.py
pytest -q tests/test_phase3_recipes.py
pytest -q tests/test_phase4_insights.py
pytest -q tests/test_phase5_auth.py
```

Optional custom base URL:

```bash
API_BASE_URL=http://localhost:8000 pytest -q tests/test_phase0_api.py
```

## License

PantryOS is licensed under the [GNU Affero General Public License v3.0 or later](LICENSE)
(`AGPL-3.0-or-later`). If you run a modified version as a network service, you must
offer its source to the people using it.

## Contributing & Security

- [`CONTRIBUTING.md`](CONTRIBUTING.md) — local checks and what never to commit.
- [`SECURITY.md`](SECURITY.md) — report vulnerabilities privately, not in public issues.
