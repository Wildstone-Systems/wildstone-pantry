#!/usr/bin/env bash
# PantryOS update supervisor (ADR 0018 §6).
#
# Transaction per update:
#   manifest check -> defer window (24h default; urgency=critical skips)
#   -> pre-update pg_dump -> pull pinned image -> recreate -> health poll
#   -> success: record + prune | failure: previous image back + DB restore.
#
# State lives in $STATE_DIR/state.json; pre-update dumps in $BACKUP_DIR.
# Postgres major upgrades are out of scope (manual, documented).

set -u -o pipefail

MANIFEST_URL="${MANIFEST_URL:?set MANIFEST_URL}"
PROJECT_DIR="${PROJECT_DIR:-/opt/pantryos}"
COMPOSE_FILE="${COMPOSE_FILE:-docker-compose.product.yml}"
STATE_DIR="${STATE_DIR:-/state}"
BACKUP_DIR="${BACKUP_DIR:-/backups}"
CHECK_INTERVAL="${CHECK_INTERVAL:-3600}"
DEFER_SECONDS="${DEFER_SECONDS:-86400}"          # 24h defer window (ADR 0018 §6)
HEALTH_TIMEOUT="${HEALTH_TIMEOUT:-180}"          # seconds to wait for healthy
UPDATE_MODE="${UPDATE_MODE:-auto}"               # auto | notify-only

STATE_FILE="$STATE_DIR/state.json"
mkdir -p "$STATE_DIR" "$BACKUP_DIR"
[ -f "$STATE_FILE" ] || echo '{}' > "$STATE_FILE"

log() { echo "[updater $(date -u +%Y-%m-%dT%H:%M:%SZ)] $*"; }

compose() { docker compose --project-directory "$PROJECT_DIR" -f "$PROJECT_DIR/$COMPOSE_FILE" "$@"; }

state_get() { jq -r ".$1 // empty" "$STATE_FILE"; }
state_set() { tmp=$(mktemp); jq ".$1 = \"$2\"" "$STATE_FILE" > "$tmp" && mv "$tmp" "$STATE_FILE"; }

current_image() {
  cid=$(compose ps -q pantryos 2>/dev/null | head -n1)
  [ -n "$cid" ] && docker inspect --format '{{.Config.Image}}' "$cid" 2>/dev/null || true
}

current_version() {
  cid=$(compose ps -q pantryos 2>/dev/null | head -n1)
  [ -n "$cid" ] && docker inspect --format '{{index .Config.Env}}' "$cid" 2>/dev/null \
    | tr ' ' '\n' | sed -n 's/^APP_VERSION=//p' | head -n1 || true
}

wait_healthy() {
  deadline=$(( $(date +%s) + HEALTH_TIMEOUT ))
  while [ "$(date +%s)" -lt "$deadline" ]; do
    cid=$(compose ps -q pantryos 2>/dev/null | head -n1)
    if [ -n "$cid" ]; then
      status=$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' "$cid" 2>/dev/null)
      [ "$status" = "healthy" ] && return 0
      [ "$status" = "none" ] && docker exec "$cid" curl -fsS http://localhost:8000/health >/dev/null 2>&1 && return 0
    fi
    sleep 5
  done
  return 1
}

pre_update_dump() {
  dump="$BACKUP_DIR/pre-update-$(date -u +%Y%m%d-%H%M%S).dump"
  log "taking pre-update dump: $dump"
  if compose exec -T postgres sh -c 'pg_dump --format=custom -U "$POSTGRES_USER" "$POSTGRES_DB"' > "$dump"; then
    echo "$dump"
    return 0
  fi
  rm -f "$dump"
  return 1
}

restore_dump() {
  dump="$1"
  log "ROLLBACK: restoring database from $dump"
  compose exec -T postgres sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "DROP SCHEMA public CASCADE; CREATE SCHEMA public;"' \
    && compose exec -T postgres sh -c 'pg_restore --no-owner -U "$POSTGRES_USER" -d "$POSTGRES_DB"' < "$dump"
}

apply_update() {
  new_ref="$1"; new_version="$2"
  prev_ref=$(current_image)
  log "updating pantryos: ${prev_ref:-<none>} -> $new_ref"

  dump=$(pre_update_dump) || { log "ABORT: pre-update dump failed; not updating."; return 1; }

  if ! PANTRYOS_IMAGE="$new_ref" compose pull pantryos; then
    log "ABORT: image pull failed; nothing changed."
    return 1
  fi
  PANTRYOS_IMAGE="$new_ref" compose up -d --no-deps pantryos

  if wait_healthy; then
    log "update OK: now running $new_version"
    state_set last_applied "$new_version"
    state_set pending_version ""
    state_set pending_since ""
    # keep the last 3 pre-update dumps
    ls -1t "$BACKUP_DIR"/pre-update-*.dump 2>/dev/null | tail -n +4 | xargs -r rm -f
    return 0
  fi

  log "update FAILED health check; rolling back to ${prev_ref:-previous image}"
  if [ -n "$prev_ref" ]; then
    PANTRYOS_IMAGE="$prev_ref" compose up -d --no-deps pantryos || log "rollback recreate failed"
  fi
  restore_dump "$dump" || log "DATABASE RESTORE FAILED — manual intervention needed; dump kept at $dump"
  wait_healthy && log "rollback complete; previous version healthy" || log "rollback did NOT reach healthy — check logs"
  return 1
}

log "supervisor started (mode=$UPDATE_MODE, defer=${DEFER_SECONDS}s, manifest=$MANIFEST_URL)"
while true; do
  manifest=$(curl -fsSL --max-time 20 "$MANIFEST_URL" 2>/dev/null) || { log "manifest fetch failed; retrying later"; sleep "$CHECK_INTERVAL"; continue; }
  version=$(echo "$manifest" | jq -r '.version // empty')
  image=$(echo "$manifest" | jq -r '.image // empty')
  digest=$(echo "$manifest" | jq -r '.digest // empty')
  urgency=$(echo "$manifest" | jq -r '.urgency // "normal"')

  if [ -z "$version" ] || [ -z "$image" ]; then
    log "manifest missing version/image; ignoring"
    sleep "$CHECK_INTERVAL"; continue
  fi
  # Pin by digest when provided (ADR 0018 §6) — a retargeted tag can't reach installs.
  ref="$image"
  [ -n "$digest" ] && ref="$image@$digest"

  running=$(current_version)
  if [ "$version" = "$running" ] || [ "$version" = "v$running" ] || [ "v$version" = "$running" ]; then
    sleep "$CHECK_INTERVAL"; continue
  fi

  if [ "$UPDATE_MODE" = "notify-only" ]; then
    log "update available: $version (notify-only mode; not applying)"
    sleep "$CHECK_INTERVAL"; continue
  fi

  pending=$(state_get pending_version)
  since=$(state_get pending_since)
  now=$(date +%s)
  if [ "$pending" != "$version" ]; then
    state_set pending_version "$version"
    state_set pending_since "$now"
    log "update available: $version (urgency=$urgency); defer window ${DEFER_SECONDS}s begins"
    [ "$urgency" != "critical" ] && { sleep "$CHECK_INTERVAL"; continue; }
    since="$now"
  fi

  age=$(( now - ${since:-$now} ))
  if [ "$urgency" = "critical" ] || [ "$age" -ge "$DEFER_SECONDS" ]; then
    apply_update "$ref" "$version" || log "update attempt failed; will retry next cycle"
  else
    log "deferring $version ($age/${DEFER_SECONDS}s elapsed)"
  fi
  sleep "$CHECK_INTERVAL"
done
