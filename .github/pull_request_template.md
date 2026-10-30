## What & why

<!-- One or two sentences. Link the issue if there is one. -->

## Checks

- [ ] Backend: `python -m pytest -q`, `check_env_parity.py`, `check_alembic_revisions.py` pass
- [ ] Frontend: `npm run test:run` and `npm run build` pass
- [ ] Follows `AGENTS.md` (service layer, feature-sliced module, no `print()`, no `phaseN` names)
- [ ] New migrations: Alembic, linear, revision id < 32 chars

## Public-repo hygiene

- [ ] No secrets, tokens or `.env*` files (only `.env.example`); `gitleaks` is clean
- [ ] Test fixtures build key-shaped values at runtime or use `REPLACED-*` placeholders
- [ ] No private/Tailscale IPs, tailnet or home hostnames, or home-directory paths (`scripts/check_publication_hygiene.py` is clean)
- [ ] No real names or personal emails of household members (use `example.com` / generic names)
- [ ] Workflow changes: least-privilege `permissions:`, actions pinned by SHA, no `pull_request_target`, no self-hosted runners for fork PRs
- [ ] New vendored assets (images, fonts) have a licence that allows redistribution, with attribution
