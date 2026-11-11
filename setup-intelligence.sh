#!/usr/bin/env bash
# Idempotent local intelligence setup. Installs only missing Python modules.
set -Eeuo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"
export HOME="$ROOT"
export PYTHONUSERBASE="$ROOT/.pyuser"
export PIP_USER=1 PIP_DISABLE_PIP_VERSION_CHECK=1 PIP_BREAK_SYSTEM_PACKAGES=1
export PATH="$PYTHONUSERBASE/bin:$HOME/.local/bin:$PATH"
PY="${PY:-python3}"
command -v "$PY" >/dev/null 2>&1 || { echo "[intelligence] Python 3 tidak ditemukan: $PY" >&2; exit 2; }
export PYTHONPATH="$ROOT:$($PY -c 'import site; print(site.getusersitepackages())' 2>/dev/null)${PYTHONPATH:+:$PYTHONPATH}"
"$PY" -m pip --version >/dev/null 2>&1 || "$PY" -m ensurepip --upgrade --user

MISSING="$("$PY" - <<'PY'
import importlib.util
packages = [
    ("langchain>=1.0", "langchain"),
    ("langchain-openai", "langchain_openai"),
    ("mem0ai", "mem0"),
    ("qdrant-client", "qdrant_client"),
    ("graphifyy[mcp]", "graphify"),
    ("mcp", "mcp"),
]
for package, module in packages:
    if importlib.util.find_spec(module) is None:
        print(package)
PY
)"
if [ -n "$MISSING" ]; then
  echo "[intelligence] memasang modul yang belum ada: $(printf '%s' "$MISSING" | tr '\n' ' ')"
  # Build once, re-use project-local user site on subsequent `bash start.sh`.
  mapfile -t MISSING_PACKAGES <<< "$MISSING"
  "$PY" -m pip install --user --break-system-packages "${MISSING_PACKAGES[@]}"
else
  echo "[intelligence] seluruh modul inti sudah terpasang; tidak ada pip upgrade"
fi

"$PY" - <<'PY'
from importlib import metadata
import importlib
checks = {
    "langchain": "langchain",
    "langchain-openai": "langchain_openai",
    "mem0ai": "mem0",
    "qdrant-client": "qdrant_client",
    "graphifyy": "graphify",
    "mcp": "mcp",
}
for package, module in checks.items():
    importlib.import_module(module)
    print(f"[intelligence] {package} {metadata.version(package)}: OK")
PY
if command -v graphify >/dev/null 2>&1; then
  graphify --version || true
else
  echo "[intelligence] Graphify package ready; CLI not exposed on PATH yet" >&2
fi
