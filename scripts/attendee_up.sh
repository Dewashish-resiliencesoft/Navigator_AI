#!/usr/bin/env bash
# Bring up the vendored Attendee fork (webpage-streamer profile).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT/vendor/attendee"
docker compose -p attendee -f dev.docker-compose.yaml -f dev.docker-compose.1080p.yaml -f ../../deploy/attendee/attendee-navigator.override.yaml -f ../../deploy/attendee/attendee-local.override.yaml --profile webpage-streamer up -d
