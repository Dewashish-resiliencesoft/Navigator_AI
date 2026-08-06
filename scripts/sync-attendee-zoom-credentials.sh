#!/usr/bin/env bash
# Copy NAVIGATOR_ZOOM_* from .env into the local Attendee project (web SDK bots).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ATTENDEE="${NAVIGATOR_ATTENDEE_COMPOSE_DIR:-$HOME/projects/attendee}"

if [[ -f "$ROOT/.env" ]]; then
  set -a
  # shellcheck disable=SC1091
  source "$ROOT/.env"
  set +a
fi

if [[ -z "${NAVIGATOR_ZOOM_CLIENT_ID:-}" || -z "${NAVIGATOR_ZOOM_CLIENT_SECRET:-}" ]]; then
  echo "Set NAVIGATOR_ZOOM_CLIENT_ID and NAVIGATOR_ZOOM_CLIENT_SECRET in $ROOT/.env first." >&2
  exit 1
fi

export NAVIGATOR_ZOOM_CLIENT_ID NAVIGATOR_ZOOM_CLIENT_SECRET
export NAVIGATOR_ATTENDEE_PROJECT_NAME="${NAVIGATOR_ATTENDEE_PROJECT_NAME:-Navigator}"

docker compose \
  -f "$ATTENDEE/dev.docker-compose.yaml" \
  -f "$ATTENDEE/local.docker-compose.yaml" \
  exec -T attendee-app-local \
  env NAVIGATOR_ZOOM_CLIENT_ID="$NAVIGATOR_ZOOM_CLIENT_ID" \
      NAVIGATOR_ZOOM_CLIENT_SECRET="$NAVIGATOR_ZOOM_CLIENT_SECRET" \
      NAVIGATOR_ATTENDEE_PROJECT_NAME="$NAVIGATOR_ATTENDEE_PROJECT_NAME" \
  python manage.py shell < "$ROOT/scripts/bootstrap_attendee_zoom.py"

echo "[zoom] Attendee project credentials synced — retry live demo"
