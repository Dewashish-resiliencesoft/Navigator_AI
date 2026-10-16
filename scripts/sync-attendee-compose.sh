#!/usr/bin/env bash
# Copy Navigator's Attendee override into the attendee-labs clone.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ATTENDEE="${ATTENDEE_COMPOSE_DIR:-$ROOT/vendor/attendee}"
SRC="$ROOT/docker/attendee-local.docker-compose.yaml"
DST="$ATTENDEE/local.docker-compose.yaml"

if [[ ! -f "$SRC" ]]; then
  echo "missing $SRC" >&2
  exit 1
fi
if [[ ! -d "$ATTENDEE" ]]; then
  echo "Attendee checkout missing at $ATTENDEE" >&2
  echo "  git submodule update --init --recursive" >&2
  exit 1
fi
cp "$SRC" "$DST"
echo "[sync] wrote $DST (ENABLE_VOICE_AGENTS=true SAVE_DEBUG_RECORDINGS=false)"
python3 "$ROOT/scripts/disable-attendee-debug-recording.py" "$ATTENDEE"
