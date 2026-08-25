#!/usr/bin/env bash
# Copy Meeting SDK / General App keys into local Attendee (web SDK bots).
# Do not pass Server-to-Server NAVIGATOR_ZOOM_CLIENT_ID — Zoom 3712s that JWT.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ATTENDEE="${NAVIGATOR_ATTENDEE_COMPOSE_DIR:-$HOME/projects/attendee}"

if [[ -f "$ROOT/.env" ]]; then
  set -a
  # shellcheck disable=SC1091
  source "$ROOT/.env"
  set +a
fi

SDK_ID="${NAVIGATOR_ZOOM_SDK_CLIENT_ID:-}"
SDK_SECRET="${NAVIGATOR_ZOOM_SDK_CLIENT_SECRET:-}"
if [[ -z "$SDK_ID" || -z "$SDK_SECRET" ]]; then
  echo "Set NAVIGATOR_ZOOM_SDK_CLIENT_ID and NAVIGATOR_ZOOM_SDK_CLIENT_SECRET in $ROOT/.env first (General App with Meeting SDK — not the S2S create/ZAK app)." >&2
  exit 1
fi
if [[ -n "${NAVIGATOR_ZOOM_CLIENT_ID:-}" && "$SDK_ID" == "$NAVIGATOR_ZOOM_CLIENT_ID" ]]; then
  echo "NAVIGATOR_ZOOM_SDK_CLIENT_ID must not equal NAVIGATOR_ZOOM_CLIENT_ID (S2S). Create a separate General App with Meeting SDK." >&2
  exit 1
fi

export NAVIGATOR_ZOOM_CLIENT_ID="$SDK_ID"
export NAVIGATOR_ZOOM_CLIENT_SECRET="$SDK_SECRET"
export NAVIGATOR_ATTENDEE_PROJECT_NAME="${NAVIGATOR_ATTENDEE_PROJECT_NAME:-Navigator}"

docker compose \
  -f "$ATTENDEE/dev.docker-compose.yaml" \
  -f "$ATTENDEE/local.docker-compose.yaml" \
  exec -T attendee-app-local \
  env NAVIGATOR_ZOOM_CLIENT_ID="$SDK_ID" \
      NAVIGATOR_ZOOM_CLIENT_SECRET="$SDK_SECRET" \
      NAVIGATOR_ATTENDEE_PROJECT_NAME="$NAVIGATOR_ATTENDEE_PROJECT_NAME" \
  python manage.py shell < "$ROOT/scripts/bootstrap_attendee_zoom.py"

echo "[zoom] Attendee project credentials synced — retry live demo"
