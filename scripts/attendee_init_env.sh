#!/usr/bin/env bash
# Generate vendor/attendee/.env (gitignored). Requires Docker.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT/vendor/attendee"
docker compose -p attendee -f dev.docker-compose.yaml run --rm --no-deps --entrypoint python attendee-app-local init_env.py | grep -E '^[A-Z_]+=' > .env
