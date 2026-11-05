#!/usr/bin/env sh
# Quick Install from a source checkout: installs the PRODUCTION stack
# (built frontend, single port, generated secrets, Postgres unpublished).
# Delegates to install-product.sh so secret generation and health waiting live
# in one place. For the hot-reload dev stack use `docker compose up` instead.
set -eu

BUILD_FROM_SOURCE=1 exec bash "$(dirname "$0")/install-product.sh" "$@"
