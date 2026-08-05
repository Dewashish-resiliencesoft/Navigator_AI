#!/bin/sh
set -e

compose_dir="${NAVIGATOR_ATTENDEE_COMPOSE_DIR:-/attendee}"
base_url="${NAVIGATOR_ATTENDEE_BASE_URL:-http://host.docker.internal:8002/api/v1}"

if [ "${NAVIGATOR_ATTENDEE_AUTOSTART:-1}" = "1" ] \
  && [ -d "$compose_dir" ] \
  && echo "$base_url" | grep -Eq 'localhost|127\.0\.0\.1|host\.docker\.internal'
then
  echo "[entrypoint] ensuring Attendee stack in $compose_dir…"
  docker compose \
    -f "$compose_dir/dev.docker-compose.yaml" \
    -f "$compose_dir/local.docker-compose.yaml" \
    --profile webpage-streamer \
    up -d || echo "[entrypoint] WARN: attendee compose failed — check Attendee clone path"
fi

exec "$@"
