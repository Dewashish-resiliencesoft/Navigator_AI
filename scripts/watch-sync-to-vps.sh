#!/usr/bin/env bash
# Watch local Navigator_AI and rsync to VPS on change (poll — no inotify required).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
FAST="$ROOT/scripts/sync-to-vps-fast.sh"
INTERVAL="${NAVIGATOR_VPS_WATCH_S:-2}"
LOG="${NAVIGATOR_VPS_SYNC_LOG:-/tmp/navigator-vps-sync.log}"

chmod +x "$FAST" 2>/dev/null || true
echo "[watch] $ROOT → VPS every ${INTERVAL}s (log $LOG)"
echo "[watch] started $(date -Iseconds)" >>"$LOG"

# Snapshot fingerprint of tracked files
fingerprint() {
  # mtimes + sizes; skip heavy/excluded dirs
  find "$ROOT" \
    \( -path "$ROOT/.venv" -o -path "$ROOT/.git" -o -path "$ROOT/graphify-out" \
       -o -path "$ROOT/node_modules" -o -path "$ROOT/navigator/client/web/node_modules" \
       -o -path "$ROOT/__pycache__" -o -path "$ROOT/.tools" \) -prune -o \
    -type f -printf '%P %T@ %s\n' 2>/dev/null | sort | md5sum | awk '{print $1}'
}

last=""
# Initial sync
"$FAST" || true
last="$(fingerprint)"

while true; do
  sleep "$INTERVAL"
  now="$(fingerprint)"
  if [[ "$now" != "$last" ]]; then
    echo "[watch] change detected $(date -Iseconds)" >>"$LOG"
    if "$FAST"; then
      last="$now"
    else
      echo "[watch] sync failed — will retry" >>"$LOG"
    fi
  fi
done
