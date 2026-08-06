#!/usr/bin/env bash
# One-shot: enable Attendee voice agents and recreate containers.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ATTENDEE="${ATTENDEE_COMPOSE_DIR:-$HOME/projects/attendee}"

"$ROOT/scripts/sync-attendee-compose.sh"

echo "[fix] recreating Attendee (ENABLE_VOICE_AGENTS=true)…"
docker compose \
  -f "$ATTENDEE/dev.docker-compose.yaml" \
  -f "$ATTENDEE/local.docker-compose.yaml" \
  --profile webpage-streamer up -d --force-recreate

echo "[fix] verify:"
docker compose \
  -f "$ATTENDEE/dev.docker-compose.yaml" \
  -f "$ATTENDEE/local.docker-compose.yaml" \
  exec attendee-app-local printenv ENABLE_VOICE_AGENTS

echo "voice-agents-v1" > "$ATTENDEE/.navigator-compose-id"
echo "[fix] done — retry live demo"
