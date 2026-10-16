#!/usr/bin/env bash
# Cursor afterFileEdit / stop: debounced fast sync to VPS.
set -euo pipefail
# Consume hook JSON (must not block stdin forever)
cat >/dev/null || true

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
FAST="$ROOT/scripts/sync-to-vps-fast.sh"
LOCK="/tmp/navigator-vps-sync.lock"
STAMP="/tmp/navigator-vps-sync.stamp"
LOG="${NAVIGATOR_VPS_SYNC_LOG:-/tmp/navigator-vps-sync.log}"

chmod +x "$FAST" 2>/dev/null || true

# Debounce: if another sync started <3s ago, skip
now=$(date +%s)
if [[ -f "$STAMP" ]]; then
  prev=$(cat "$STAMP" 2>/dev/null || echo 0)
  if (( now - prev < 3 )); then
    echo '{}'
    exit 0
  fi
fi
echo "$now" >"$STAMP"

(
  flock -n 9 || exit 0
  "$FAST" || echo "[hook] sync failed $(date -Iseconds)" >>"$LOG"
) 9>"$LOCK"

echo '{}'
exit 0
