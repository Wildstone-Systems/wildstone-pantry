# Development

Read [`AGENTS.md`](../AGENTS.md) (repo rules) and
[`ARCHITECTURE.md`](../ARCHITECTURE.md) first.

## Dev stack (Docker)

```bash
docker compose up --build
```

| Service | Address |
|---|---|
| Frontend (Vite, hot reload) | <http://localhost:5173> |
| Backend API | <http://localhost:8000> (API docs at `/docs`) |
| Postgres | `localhost:5432` (loopback only, dev credentials) |

Migrations run automatically when the backend starts.
This is for development only (Vite dev server, well-known credentials). To
install PantryOS, use `./scripts/install.sh` instead — see
[Getting started](getting-started.md#docker-compose).

## Backend without Docker

```bash
cd backend
python -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
cp ../.env.example .env
alembic upgrade head
uvicorn main:app --reload --host 0.0.0.0 --port 8000
```

Seed default categories and locations:

```bash
cd backend
python ../scripts/seed.py
```

## Frontend without Docker

```bash
cd frontend
npm ci
npm run dev
```

Vite proxies `/api` to `http://localhost:8000` (change with `VITE_API_PROXY_TARGET`).

## Tests and checks

Run these before pushing — CI (`.github/workflows/ci.yml`) runs the same ones.

```bash
cd backend
python -m pytest -q
python scripts/check_env_parity.py
python scripts/check_alembic_revisions.py
```

```bash
cd frontend
npm run test:run
npm run build
```

- Backend tests use an in-process `TestClient` + isolated SQLite. No running
  server needed.
- Frontend tests are Vitest files next to the code (`*.test.tsx`).

## QA gate

One command for the full check (feedback inbox, frontend tests + build,
isolated integration tests, smoke test):

```bash
./scripts/qa.sh
```

- `QA_API_BASE_URL` — API to smoke-test (default `http://localhost:8000`)
- `QA_SKIP_SMOKE=1` — skip the smoke test

List open in-app feedback:

```bash
python scripts/check_feedback.py --status open
```

## Isolated test stack

A fresh stack on separate ports and volume that runs `pytest` in a container:

```bash
docker compose -f docker-compose.test.yml up --build --abort-on-container-exit --exit-code-from tests
```

Ports: Postgres `15432`, API `18000`, frontend `15173`.

## Smoke test

Runs an end-to-end flow (login, items, inventory, receipts, recipes, insights)
against a live API backed by Postgres:

```bash
python scripts/smoke_test.py
```

## API reference

The API is self-documenting. With the backend running, open:

- `/docs` — interactive Swagger UI
- `/redoc` — reference view
- `/openapi.json` — raw schema

`backend/tests/api_surface.txt` is a checked-in snapshot of every route
(regenerate with `backend/scripts/generate_api_surface.py`).
