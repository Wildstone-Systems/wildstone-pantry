# Long-lived deployments (staging)

For everyday household use, the product install in
[Getting started](getting-started.md) is all you need. This page covers the
**staging stack** — a long-lived install built from source, for example on a
home server, that never collides with the dev or test stacks.

```bash
docker compose --env-file .env.staging -f docker-compose.staging.yml up --build -d
```

| Service | Port |
|---|---|
| Frontend (nginx, production build) | `25173` (all interfaces) |
| Backend API | `28000` (all interfaces) |
| Postgres | `127.0.0.1:25432` (loopback only — use an SSH tunnel) |

## Secrets

- Copy `.env.example` to an untracked `.env.staging` and fill it in.
- The compose file forces `ENVIRONMENT=staging`, so the stack can't start in
  development mode by accident.
- In `staging`/`production` the backend **refuses to boot on insecure
  defaults** — `DATABASE_URL` and `AUTH_PERSONAL_PASSWORD` must be real values
  (see `_validate_protected_env` in `backend/app/core/config.py`).

## Networking

- nginx serves the UI and proxies `/api` to the backend, so the UI works from
  any hostname or IP.
- To let browsers on other hosts call the API directly, set `CORS_ALLOW_ORIGINS`.
- PantryOS is designed for a trusted home network. Don't expose it directly to
  the internet (see [`SECURITY.md`](../SECURITY.md)).
