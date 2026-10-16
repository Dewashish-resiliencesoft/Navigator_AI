#!/usr/bin/env bash
# Local dev: Python reload + dashboard rebuild.
# Frontend saves rebuild dist and open /client tabs reload themselves.
# Python saves restart uvicorn. A Cloudflare quick tunnel is owned by that
# process, so a Python restart mints a new public URL.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

export DISPLAY="${DISPLAY:-:0}"
export PYTHONUNBUFFERED=1
export NAVIGATOR_WARM_POOL="${NAVIGATOR_WARM_POOL:-1}"
export NAVIGATOR_DEV_WATCH=1

WEB_PID=""
cleanup() {
  if [[ -n "${WEB_PID}" ]]; then
    kill "${WEB_PID}" 2>/dev/null || true
    wait "${WEB_PID}" 2>/dev/null || true
  fi
}
trap cleanup EXIT INT TERM

echo "[dev-watch] dashboard: navigator/client/web (vite build --watch)"
echo "[dev-watch] api: uvicorn --reload on navigator/*.py"
(cd "$ROOT/navigator/client/web" && npm run watch) &
WEB_PID=$!

.venv/bin/uvicorn navigator.app.main:app \
  --host 0.0.0.0 \
  --port 8000 \
  --workers 1 \
  --reload \
  --reload-dir "$ROOT/navigator"
