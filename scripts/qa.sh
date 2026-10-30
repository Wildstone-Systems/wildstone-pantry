#!/usr/bin/env sh
set -eu

ROOT_DIR=$(CDPATH= cd "$(dirname "$0")/.." && pwd)

docker_cmd() {
  if command -v docker >/dev/null 2>&1; then
    docker "$@"
    return
  fi

  if command -v flatpak-spawn >/dev/null 2>&1; then
    flatpak-spawn --host docker "$@"
    return
  fi

  echo "docker is required (or VS Code Flatpak with flatpak-spawn available)" >&2
  exit 1
}

compose_cmd() {
  docker_cmd compose "$@"
}

npm_cmd() {
  if command -v npm >/dev/null 2>&1; then
    npm "$@"
    return
  fi

  if command -v flatpak-spawn >/dev/null 2>&1; then
    flatpak-spawn --host npm "$@"
    return
  fi

  echo "npm is required (or VS Code Flatpak with flatpak-spawn available)" >&2
  exit 1
}

api_up() {
  url=$1
  if command -v curl >/dev/null 2>&1; then
    curl -sf "$url" >/dev/null 2>&1
    return
  fi
  python3 -c "import urllib.request; urllib.request.urlopen('$url', timeout=2).read()" >/dev/null 2>&1
}

wait_for_api() {
  url=$1
  tries=${2:-30}
  while [ "$tries" -gt 0 ]; do
    if api_up "$url"; then
      return 0
    fi
    tries=$((tries - 1))
    sleep 2
  done
  return 1
}

QA_API_BASE_URL=${QA_API_BASE_URL:-${API_BASE_URL:-http://localhost:8000}}

HEALTH_URL="${QA_API_BASE_URL%/}/health"

echo "[qa] Ensuring dev API is reachable for feedback triage: $HEALTH_URL"
if ! api_up "$HEALTH_URL"; then
  echo "[qa] API not reachable; starting dev stack via docker compose..."
  (cd "$ROOT_DIR" && compose_cmd up --build -d)
  wait_for_api "$HEALTH_URL" 40
fi

echo "[qa] Feedback triage (open items)"
python3 "$ROOT_DIR/scripts/check_feedback.py" --base-url "$QA_API_BASE_URL" --status open --limit 50

echo "[qa] Frontend unit tests"
(cd "$ROOT_DIR/frontend" && npm_cmd test)

echo "[qa] Frontend build (typecheck + production bundle)"
(cd "$ROOT_DIR/frontend" && npm_cmd run build)

echo "[qa] Integration tests (docker-compose.test.yml)"
cleanup_test_stack() {
  compose_cmd -f "$ROOT_DIR/docker-compose.test.yml" down -v >/dev/null 2>&1 || true
}
trap cleanup_test_stack EXIT INT TERM
cleanup_test_stack
compose_cmd -f "$ROOT_DIR/docker-compose.test.yml" up --build -d postgres backend
wait_for_api "http://localhost:18000/health" 40
compose_cmd -f "$ROOT_DIR/docker-compose.test.yml" run --rm --build tests

if [ "${QA_SKIP_SMOKE:-}" = "1" ]; then
  echo "[qa] Skipping smoke test (QA_SKIP_SMOKE=1)"
  cleanup_test_stack
  trap - EXIT INT TERM
  exit 0
fi

echo "[qa] Smoke test (test stack)"
API_BASE_URL="http://localhost:18000" python3 "$ROOT_DIR/scripts/smoke_test.py"

cleanup_test_stack
trap - EXIT INT TERM
