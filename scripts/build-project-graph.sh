#!/usr/bin/env bash
# Build a local, code-only Graphify graph for Kwee's MCP stdio server.
set -Eeuo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
if ! command -v graphify >/dev/null 2>&1; then
  echo "[graphify] CLI belum ada. Jalankan: bash setup-intelligence.sh" >&2
  exit 2
fi
mkdir -p graphify-out
# This is deterministic AST extraction; no LLM key and no remote code upload.
graphify extract . --code-only --no-viz
if [ ! -s graphify-out/graph.json ]; then
  echo "[graphify] graphify selesai tetapi graphify-out/graph.json tidak ditemukan/kosong" >&2
  exit 3
fi
printf '[graphify] graph siap: %s\n' "$ROOT/graphify-out/graph.json"
