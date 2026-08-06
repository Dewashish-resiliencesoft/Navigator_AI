#!/usr/bin/env bash
# Copy Navigator's Attendee override into the attendee-labs clone.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ATTENDEE="${ATTENDEE_COMPOSE_DIR:-$HOME/projects/attendee}"
SRC="$ROOT/docker/attendee-local.docker-compose.yaml"
DST="$ATTENDEE/local.docker-compose.yaml"

if [[ ! -f "$SRC" ]]; then
  echo "missing $SRC" >&2
  exit 1
fi
if [[ ! -d "$ATTENDEE" ]]; then
  echo "Attendee clone missing at $ATTENDEE" >&2
  echo "  git clone https://github.com/attendee-labs/attendee $ATTENDEE" >&2
  exit 1
fi
cp "$SRC" "$DST"
echo "[sync] wrote $DST (ENABLE_VOICE_AGENTS=true)"
