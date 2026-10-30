# PantryOS desktop launcher (ADR 0018 §4)

Tauri v2 app: **Download → Install → Open**. Detects Docker, generates the
instance config (secrets never shown), starts the product compose stack,
waits for `/health`, opens the browser. Docker is never named in the UI
outside diagnostics; the in-app first-run wizard handles everything else.

- `ui/` — the three-screen setup wizard (plain HTML/JS, `withGlobalTauri`).
- `src-tauri/` — Rust core: `docker_status`, `install_stack` (config-dir env
  generation with preserved secrets on re-run, port pick with conflict
  fallback, pull/up, health wait), `stack_status`, `start/stop`,
  `diagnostics` (never includes `.env`), `open_app`.
- `src-tauri/compose/docker-compose.product.yml` — the embedded, image-only
  stack template (kept in sync with the repo-root product compose).

## Build

The dev sandbox has no webkit toolchain — build on a real desktop OS, or let
CI do it (`.github/workflows/launcher.yml` builds unsigned Windows + macOS
bundles on `workflow_dispatch` or a `launcher-v*` tag).

Locally (Windows/macOS, Rust + Node installed):

```bash
cd launcher
npx @tauri-apps/cli@^2 dev     # run against the wizard UI
npx @tauri-apps/cli@^2 build   # produce installers (unsigned until beta)
```

## Deliberate v1 scope (ADR 0018 §4)

- Docker Desktop is detected and the user is pointed at docker.com when
  missing; managed install (winget/brew) and a tray mode are the next
  iterations, after the first signed beta.
- Updates are the in-stack supervisor's job (`--profile auto-update`); the
  launcher only reports status via `/api/system/version`.
- Signing: budgeted at beta — Apple Developer + Azure Trusted Signing secrets
  get wired into the workflow; unsigned artifacts are for testing only.
