# Contributing to PantryOS

Thanks for helping! Short version:

1. Read [`AGENTS.md`](AGENTS.md) (repo rules) and [`ARCHITECTURE.md`](ARCHITECTURE.md).
2. Branch from `main`, keep the change focused, open a PR using the template.
3. Run the checks below before pushing — CI runs the same ones.

## Local checks

```bash
# Backend
cd backend && python -m pytest -q
python scripts/check_env_parity.py && python scripts/check_alembic_revisions.py

# Frontend
cd frontend && npm ci && npm run test:run && npm run build

# Public-repo hygiene (from repo root)
python3 scripts/check_publication_hygiene.py
```

## Never commit

- Real secrets or `.env` files (only `.env.example` is tracked).
- Details of a real install: IP addresses, hostnames, tailnet names, home
  directory paths, real people's names or emails. Use placeholders such as
  `<household-host>`, `<owner>`, `admin@example.com`.
- Key-shaped literals in tests — build them at runtime or use a
  `REPLACED-*` placeholder so secret scanners stay quiet.

## Licence of contributions

By contributing you agree your work is licensed under the project licence,
[AGPL-3.0-or-later](LICENSE).

## Security issues

See [`SECURITY.md`](SECURITY.md) — report privately, not in a public issue.
