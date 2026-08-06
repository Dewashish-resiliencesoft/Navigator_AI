#!/bin/sh
set -e

compose_dir="${NAVIGATOR_ATTENDEE_COMPOSE_DIR:-/attendee}"
base_url="${NAVIGATOR_ATTENDEE_BASE_URL:-http://host.docker.internal:8002/api/v1}"
compose_id="voice-agents-v1"
marker="$compose_dir/.navigator-compose-id"

if [ "${NAVIGATOR_ATTENDEE_AUTOSTART:-1}" = "1" ] \
  && [ -d "$compose_dir" ] \
  && echo "$base_url" | grep -Eq 'localhost|127\.0\.0\.1|host\.docker\.internal'
then
  if [ -f /app/docker/attendee-local.docker-compose.yaml ]; then
    cp /app/docker/attendee-local.docker-compose.yaml "$compose_dir/local.docker-compose.yaml"
  fi

  recreate=""
  if [ ! -f "$marker" ] || [ "$(cat "$marker" 2>/dev/null)" != "$compose_id" ]; then
    recreate="--force-recreate"
    echo "[entrypoint] Attendee needs recreate (ENABLE_VOICE_AGENTS)…"
  fi

  echo "[entrypoint] ensuring Attendee stack in $compose_dir…"
  # shellcheck disable=SC2086
  docker compose \
    -f "$compose_dir/dev.docker-compose.yaml" \
    -f "$compose_dir/local.docker-compose.yaml" \
    --profile webpage-streamer \
    up -d $recreate \
    || echo "[entrypoint] WARN: attendee compose failed — check Attendee clone + docker.sock"

  if [ -n "$recreate" ]; then
    echo "$compose_id" > "$marker" 2>/dev/null || true
  fi
fi

exec "$@"
