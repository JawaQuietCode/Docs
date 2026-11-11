#!/usr/bin/env bash
# canvas.sh — pintasan CLI untuk engine OpenHands (@openhands/agent-canvas).
#
#   ./canvas.sh status
#   ./canvas.sh start
#   ./canvas.sh stop
#   ./canvas.sh ask "perbaiki test yang gagal"
#
# Semua logika ada di agent/canvas_engine.py (dipakai juga oleh bot lewat
# perintah !canvas dan mekanisme `canvas`).
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"
PY="${KWEE_PY:-python3}"
exec "$PY" -m agent.canvas_engine "$@"
