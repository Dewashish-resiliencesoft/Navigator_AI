#!/usr/bin/env bash
# Sync this device's Navigator_AI → LAN VPS, keep Attendee up, restart uvicorn + warm pool.
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

RSYNC_SSH='ssh -o BatchMode=yes -o ConnectTimeout=20 -o StrictHostKeyChecking=accept-new'
REMOTE="${NAVIGATOR_VPS_USER}@${NAVIGATOR_VPS_HOST}"

echo "[vps] sync $ROOT → $REMOTE:$NAVIGATOR_VPS_PATH"
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
  echo "[vps] sync .env (this device wins)"
  rsync -az -e "$RSYNC_SSH" "$ROOT/.env" "$REMOTE:$NAVIGATOR_VPS_PATH/.env"
fi

echo "[vps] patch host flags + Attendee up + restart uvicorn"
ssh -o BatchMode=yes -o ConnectTimeout=20 "$REMOTE" "bash -s" <<REMOTE_SCRIPT
set -euo pipefail
cd "$NAVIGATOR_VPS_PATH"
ATT="/home/aneesh/projects/attendee"

patch_env() {
  local key="\$1" val="\$2"
  if grep -q "^\${key}=" .env 2>/dev/null; then
    sed -i "s|^\${key}=.*|\${key}=\${val}|" .env
  else
    echo "\${key}=\${val}" >> .env
  fi
}
touch .env
patch_env NAVIGATOR_ATTENDEE_BASE_URL "http://127.0.0.1:8002/api/v1"
patch_env NAVIGATOR_ATTENDEE_COMPOSE_DIR "\$ATT"
patch_env NAVIGATOR_ATTENDEE_AUTOSTART "0"
patch_env NAVIGATOR_WARM_POOL "1"
patch_env NAVIGATOR_HEADFUL "0"
# Preserve VPS Attendee token if this host has one (local .env may still carry an old key)
if [[ -f "\$HOME/.config/navigator/attendee_api_key" ]]; then
  _nav_key=\$(tr -d '[:space:]' < "\$HOME/.config/navigator/attendee_api_key")
  patch_env NAVIGATOR_ATTENDEE_API_KEY "\$_nav_key"
fi

if [[ -f "\$ATT/dev.docker-compose.yaml" ]]; then
  if [[ -x scripts/sync-attendee-compose.sh ]]; then
    ATTENDEE_COMPOSE_DIR="\$ATT" ./scripts/sync-attendee-compose.sh || true
  fi
  sg docker -c "docker compose -f \$ATT/dev.docker-compose.yaml -f \$ATT/local.docker-compose.yaml --profile webpage-streamer up -d"
fi

# Virtual display for warm Playwright (headless host)
if ! pgrep -x Xvfb >/dev/null; then
  if command -v Xvfb >/dev/null; then
    nohup Xvfb :99 -screen 0 1920x1080x24 -ac >> /tmp/navigator-xvfb.log 2>&1 &
    sleep 1
  else
    echo "[vps] WARN: Xvfb missing — warm parked browser needs: sudo apt install xvfb"
  fi
fi
export DISPLAY=:99

pkill -f 'uvicorn navigator.app.main:app' 2>/dev/null || true
sleep 2
pkill -9 -f 'uvicorn navigator.app.main:app' 2>/dev/null || true
sleep 1
nohup env DISPLAY=:99 .venv/bin/uvicorn navigator.app.main:app --host 0.0.0.0 --port 8000 --workers 1 \\
  >> /tmp/navigator-uvicorn.log 2>&1 &
sleep 5
pgrep -af 'uvicorn navigator.app.main' || { echo 'uvicorn failed to start'; tail -40 /tmp/navigator-uvicorn.log; exit 1; }
code=\$(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8000/docs || echo 000)
echo "docs \$code"
sg docker -c "docker ps --format 'table {{.Names}}\t{{.Status}}'" | head -20 || true
REMOTE_SCRIPT

echo "[vps] done — http://${NAVIGATOR_VPS_HOST}:8000/client"
