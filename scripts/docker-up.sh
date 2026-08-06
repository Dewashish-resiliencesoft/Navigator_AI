#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ATTENDEE="${ATTENDEE_COMPOSE_DIR:-$HOME/projects/attendee}"

if [[ ! -f "$ATTENDEE/dev.docker-compose.yaml" ]]; then
  echo "Attendee clone missing at $ATTENDEE"
  echo "  git clone https://github.com/attendee-labs/attendee $ATTENDEE"
  exit 1
fi

"$ROOT/scripts/sync-attendee-compose.sh"

echo "[up] Attendee stack (voice agents enabled)…"
docker compose \
  -f "$ATTENDEE/dev.docker-compose.yaml" \
  -f "$ATTENDEE/local.docker-compose.yaml" \
  --profile webpage-streamer up -d --force-recreate

echo "[up] Navigator…"
export ATTENDEE_COMPOSE_DIR="$ATTENDEE"
cd "$ROOT"
docker compose up -d "$@"

echo "[up] done — Navigator http://127.0.0.1:8080/client · Attendee http://127.0.0.1:8002"
