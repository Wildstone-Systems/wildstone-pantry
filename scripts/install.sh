#!/usr/bin/env sh
set -eu

if ! command -v docker >/dev/null 2>&1; then
  echo "docker is required" >&2
  exit 1
fi

if ! docker compose version >/dev/null 2>&1; then
  echo "docker compose plugin is required" >&2
  exit 1
fi

echo "Starting PantryOS stack..."
docker compose up --build -d

echo "PantryOS started."
echo "API: http://localhost:8000"
echo "UI : http://localhost:5173"
