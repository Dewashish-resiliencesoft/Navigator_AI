#!/usr/bin/env bash
# Fast code sync only (no docker recreate / uvicorn kill). Pair with --reload on VPS.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ENV_FILE="${NAVIGATOR_VPS_ENV:-$HOME/.cursor/navigator-vps.env}"

if [[ ! -f "$ENV_FILE" ]]; then
  echo "missing $ENV_FILE" >&2
  exit 1
fi
set -a
# shellcheck disable=SC1090
source "$ENV_FILE"
set +a

: "${NAVIGATOR_VPS_HOST:?}"
: "${NAVIGATOR_VPS_USER:?}"
: "${NAVIGATOR_VPS_PATH:?}"

RSYNC_SSH='ssh -o BatchMode=yes -o ConnectTimeout=15 -o StrictHostKeyChecking=accept-new'
REMOTE="${NAVIGATOR_VPS_USER}@${NAVIGATOR_VPS_HOST}"
LOG="${NAVIGATOR_VPS_SYNC_LOG:-/tmp/navigator-vps-sync.log}"

{
  echo "[$(date -Iseconds)] fast-sync → $REMOTE:$NAVIGATOR_VPS_PATH"
  rsync -az -e "$RSYNC_SSH" \
    --exclude '.venv/' \
    --exclude 'graphify-out/' \
    --exclude 'graphify/' \
    --exclude '__pycache__/' \
    --exclude '.git/' \
    --exclude 'node_modules/' \
    --exclude 'navigator/client/web/node_modules/' \
    --exclude '*.pyc' \
    --exclude '*.db' \
    --exclude '*.db-wal' \
    --exclude '*.db-shm' \
    --exclude '.tools/' \
    --exclude '.pytest_cache/' \
    --exclude '.mypy_cache/' \
    --exclude '*.egg-info/' \
    "$ROOT/" "$REMOTE:$NAVIGATOR_VPS_PATH/"

  if [[ -f "$ROOT/.env" ]]; then
    rsync -az -e "$RSYNC_SSH" "$ROOT/.env" "$REMOTE:$NAVIGATOR_VPS_PATH/.env"
  fi

  # Re-assert host-local flags without restarting (uvicorn --reload picks code up)
  ssh -o BatchMode=yes -o ConnectTimeout=15 "$REMOTE" "bash -s" <<'REMOTE_SCRIPT'
set -euo pipefail
cd /home/aneesh/Navigator_AI
ATT=/home/aneesh/projects/attendee
patch_env() {
  local key="$1" val="$2"
  if grep -q "^${key}=" .env 2>/dev/null; then
    sed -i "s|^${key}=.*|${key}=${val}|" .env
  else
    echo "${key}=${val}" >> .env
  fi
}
touch .env
patch_env NAVIGATOR_ATTENDEE_BASE_URL "http://127.0.0.1:8002/api/v1"
patch_env NAVIGATOR_ATTENDEE_COMPOSE_DIR "$ATT"
patch_env NAVIGATOR_ATTENDEE_AUTOSTART "0"
patch_env NAVIGATOR_WARM_POOL "1"
patch_env NAVIGATOR_HEADFUL "0"
if [[ -f "$HOME/.config/navigator/attendee_api_key" ]]; then
  _nav_key=$(tr -d '[:space:]' < "$HOME/.config/navigator/attendee_api_key")
  patch_env NAVIGATOR_ATTENDEE_API_KEY "$_nav_key"
fi
REMOTE_SCRIPT
  echo "[$(date -Iseconds)] ok"
} >>"$LOG" 2>&1
