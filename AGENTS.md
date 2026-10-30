# PantryOS – Agent & Contributor Guide

Conventions for anyone (human or agent) changing this repo. Architecture detail
lives in `ARCHITECTURE.md`.

## Core rules
1. **Service layer only.** No direct DB access from routes. Routes → services →
   repository → ORM.
2. **Feature-sliced.** New backend functionality goes in `backend/modules/<domain>/`,
   registered via a `ModuleManifest`. Do not edit `app/main.py` to add routes.
   New frontend functionality goes in `frontend/src/modules/<domain>/`.
3. **One owner per table.** Cross-module access goes through a service interface
   or a published event — never by importing another module's models/repository.
4. **No `print()`.** Use `logging`. No bare `except:`.
5. **No `phaseN` naming** in source/modules/new tests. Name by feature/domain.
6. **Secrets.** Only `.env.example` is committed. Never commit `.env*`. In
   `staging`/`production` the app must refuse to start on insecure defaults.
7. **Migrations.** Alembic, linear graph, revision id `< 32` chars (detail goes
   in the filename).

## Tests
- Backend: in-process `TestClient` + isolated SQLite (`backend/conftest.py`).
  No live server, no DB mocking. Cross-module tests in `backend/tests/`,
  module-owned tests co-located in the module.
- Frontend: Vitest, co-located `*.test.tsx`.

## Before pushing
- Backend: `python -m pytest -q`, `python scripts/check_env_parity.py`,
  `python scripts/check_alembic_revisions.py`.
- Frontend: `npm run test:run`, `npm run build`.
- These all run in CI (`.github/workflows/ci.yml`).

## Feedback-aware workflow
Review open in-app feedback (`scripts/check_feedback.py` or the Feedback Inbox)
before and after substantial changes; run the QA gate (`scripts/qa.sh`).
