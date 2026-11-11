#!/usr/bin/env bash
# setup.sh — siapkan dependensi kweewind blows di direktori proyek ini.
#
# Yang ditambahkan dibanding versi lama:
#   * Pillow (dipakai agent/llm/client.py untuk memperkecil gambar vision) dan
#     yt-dlp (media) ikut dipasang — dulu vision mengirim base64 mentah dan
#     media hanya jalan kalau binary-nya kebetulan ada.
#   * Playwright + Chromium: wajib untuk tool `browser`. Kegagalan unduh Chromium
#     TIDAK mematikan setup (bot tetap bisa jalan tanpa browser), tetapi
#     dilaporkan jelas.
#   * ACTION_TOKEN dibuat kalau belum ada (transport ↔ inti agent).
#   * laporan kemampuan (curl/git/jq/ffmpeg/yt-dlp/playwright) di akhir.
#   * self-test tool `shell` benar-benar menjalankan `echo` — menangkap masalah
#     "allowlist kosong / config tidak terbaca" saat setup.
set -Eeuo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"
export HOME="$ROOT"
export TMPDIR="$ROOT/.tmp"
export PYTHONUSERBASE="$ROOT/.pyuser"
export PIP_USER=1
export PIP_DISABLE_PIP_VERSION_CHECK=1
export PIP_BREAK_SYSTEM_PACKAGES=1
export PYTHONUNBUFFERED=1
export KWEE_ROOT="$ROOT"
mkdir -p "$TMPDIR" "$PYTHONUSERBASE" "$ROOT/data/media/files" "$ROOT/data/media/assets" "$ROOT/data/inbox" "$ROOT/data/browser"
PY="${PY:-python3}"
fail() { echo "[setup] ERROR: $*" >&2; exit 1; }
log() { echo "[setup] $*"; }

# Native RTK rewrite: helper keeps installation and smoke-test logic testable.
. "$ROOT/scripts/setup_rtk.sh"
kwee_setup_rtk
command -v node >/dev/null 2>&1 || fail "Node.js tidak ditemukan. Pilih image Pterodactyl yang menyediakan Node.js >=20."
command -v npm >/dev/null 2>&1 || fail "npm tidak ditemukan."
command -v "$PY" >/dev/null 2>&1 || fail "Python tidak ditemukan: $PY"
PY="$(command -v "$PY")"
export KWEE_PY="$PY"
PYTHON_USER_SITE="$("$PY" -c 'import site; print(site.getusersitepackages())' 2>/dev/null || true)"
DEFAULT_USER_SITE="$(env -u PYTHONUSERBASE "$PY" -c 'import site; print(site.getusersitepackages())' 2>/dev/null || true)"
for _site in "$PYTHON_USER_SITE" "$DEFAULT_USER_SITE"; do
  [ -n "$_site" ] && case ":$PYTHONPATH:" in *":$_site:"*) ;; *) export PYTHONPATH="$ROOT:$_site${PYTHONPATH:+:$PYTHONPATH}" ;; esac
done

# Instalasi Node yang gagal harus terlihat, tidak disamarkan oleh pipe ke tail.
log "memasang dependensi Node..."
if [ -f package-lock.json ]; then
  if ! npm ci --include=dev --no-audit --no-fund >"$TMPDIR/npm-setup.log" 2>&1; then
    tail -40 "$TMPDIR/npm-setup.log" >&2
    fail "npm ci gagal. Periksa log npm di atas; cek koneksi/registry dan package-lock.json."
  fi
else
  if ! npm install --include=dev --no-audit --no-fund >"$TMPDIR/npm-setup.log" 2>&1; then
    tail -40 "$TMPDIR/npm-setup.log" >&2
    fail "npm install gagal."
  fi
fi
if ! node --input-type=module -e "await import('@itsliaaa/baileys'); await import('@hapi/boom'); await import('pino')" >/dev/null 2>&1; then
  log "memasang Baileys, @hapi/boom, dan pino..."
  if ! npm install --no-save --no-package-lock --include=dev --no-audit --no-fund \
      '@itsliaaa/baileys@0.3.18-final' '@hapi/boom@^10.0.1' 'pino@^9.7.0' >"$TMPDIR/npm-core.log" 2>&1; then
    tail -40 "$TMPDIR/npm-core.log" >&2
    fail "Pemasangan package Node inti gagal."
  fi
fi
node --input-type=module -e "await import('@itsliaaa/baileys'); await import('@hapi/boom'); await import('pino'); console.log('[setup] modul Node utama OK')" \
  || fail "Baileys/dependensi Node belum dapat diimpor setelah instalasi."

# Playwright: dibutuhkan tool `browser`. Gagal di sini bukan alasan menghentikan
# setup — bot tetap bisa dipakai tanpa browser, jadi hanya dilaporkan.
if ! node --input-type=module -e "await import('playwright')" >/dev/null 2>&1; then
  log "memasang Playwright untuk tool browser..."
  if ! npm install --no-save --no-package-lock --include=dev --no-audit --no-fund \
      '@itsliaaa/baileys@0.3.18-final' '@hapi/boom@^10.0.1' 'pino@^9.7.0' \
      'playwright@^1.64.0' >"$TMPDIR/npm-playwright.log" 2>&1; then
    tail -30 "$TMPDIR/npm-playwright.log" >&2
    log "PERINGATAN: Playwright gagal dipasang — tool browser tidak akan bekerja"
  fi
fi
if node --input-type=module -e "await import('playwright')" >/dev/null 2>&1; then
  log "mengunduh Chromium Playwright (bisa lama pada koneksi lambat)..."
  npx --yes playwright install chromium >"$TMPDIR/playwright-chromium.log" 2>&1 \
    || log "PERINGATAN: Chromium gagal diunduh — tool browser tidak akan bekerja (lihat $TMPDIR/playwright-chromium.log)"
fi

# pip --user diarahkan ke .pyuser agar tidak membutuhkan root dan tidak memakai
# /home/container secara hard-coded.
if ! "$PY" -m pip --version >/dev/null 2>&1; then
  "$PY" -m ensurepip --upgrade --user >/dev/null 2>&1 || true
fi
"$PY" -m pip --version >/dev/null 2>&1 || fail "pip tidak tersedia untuk $PY dan ensurepip gagal."
log "memasang dependensi Python..."
if ! "$PY" -m pip install --user --break-system-packages \
    "fastapi>=0.115.0" "uvicorn>=0.30.6" httpx==0.27.2 pyyaml==6.0.2 pillow==10.4.0 \
    >"$TMPDIR/pip-setup.log" 2>&1; then
  tail -40 "$TMPDIR/pip-setup.log" >&2
  fail "pip install gagal."
fi
PYTHONPATH="$ROOT${PYTHONPATH:+:$PYTHONPATH}" "$PY" -c 'import fastapi, uvicorn, httpx, yaml; print("[setup] modul Python utama OK")' \
  || fail "Python deps terpasang tetapi tidak dapat diimpor."
# yt-dlp: opsional (tool media). Dipasang best-effort.
"$PY" -m pip install --user --break-system-packages --upgrade yt-dlp >"$TMPDIR/pip-ytdlp.log" 2>&1 \
  && log "yt-dlp terpasang" \
  || log "PERINGATAN: yt-dlp gagal dipasang — unduh media dari situs tertentu tidak akan bekerja"

# --------------------------------------------------------------------------- #
# ENGINE OPENHANDS (@openhands/agent-canvas) — BAWAAN
#
# Engine tugas berat Kwee adalah OpenHands. Dipasang di sini supaya `kwee ask`
# langsung bekerja setelah setup, tanpa langkah tambahan. Kegagalan pemasangan
# tidak mematikan setup: bot tetap jalan dengan engine `native`.
# Matikan dengan KWEE_CANVAS=0.
# --------------------------------------------------------------------------- #
CANVAS_ON="${KWEE_CANVAS:-0}"
case "$CANVAS_ON" in 0|false|off|no) CANVAS_ON=0 ;; *) CANVAS_ON=1 ;; esac
if [ "$CANVAS_ON" = "1" ]; then
  if ! command -v agent-canvas >/dev/null 2>&1; then
    log "memasang @openhands/agent-canvas (global) — butuh Node >= 24"
    npm install -g @openhands/agent-canvas >"$TMPDIR/npm-canvas.log" 2>&1 \
      && log "agent-canvas terpasang: $(agent-canvas --version 2>/dev/null | head -1)" \
      || { tail -30 "$TMPDIR/npm-canvas.log" >&2 || true; log "PERINGATAN: agent-canvas gagal dipasang — engine canvas tidak tersedia"; }
  else
    log "agent-canvas sudah ada: $(agent-canvas --version 2>/dev/null | head -1)"
  fi
  if ! command -v uvx >/dev/null 2>&1; then
    if command -v curl >/dev/null 2>&1; then
      log "memasang uv (agent-server dijalankan lewat uvx)"
      curl -LsSf --max-time 120 https://astral.sh/uv/install.sh | sh >"$TMPDIR/uv-install.log" 2>&1 \
        && log "uv terpasang" \
        || { tail -20 "$TMPDIR/uv-install.log" >&2 || true; log "PERINGATAN: uv gagal dipasang"; }
      export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"
    else
      log "PERINGATAN: curl tidak ada; uv tidak bisa dipasang otomatis"
    fi
  else
    log "uvx tersedia: $(uvx --version 2>/dev/null | head -1)"
  fi
fi

if [ -f "$ROOT/scheduler/cron.py" ] && [ ! -f "$ROOT/scheduler/__init__.py" ]; then
  printf '%s\n' '"""Local Kwee scheduler package."""' > "$ROOT/scheduler/__init__.py"
fi
[ -f "$ROOT/scheduler/cron.py" ] || fail "scheduler/cron.py tidak ditemukan. Upload folder scheduler sebelum menjalankan bot."

# --------------------------------------------------------------------------- #
# TOKEN AGENT (transport ↔ inti agent)
# --------------------------------------------------------------------------- #
if [ -f "$ROOT/.env" ] && ! grep -q '^ACTION_TOKEN=' "$ROOT/.env"; then
  TOKEN="$(node -e "console.log(require('crypto').randomBytes(24).toString('hex'))" 2>/dev/null || true)"
  if [ -n "$TOKEN" ]; then
    printf '\n# Dibuat otomatis oleh setup.sh — token transport <-> inti agent\nACTION_TOKEN=%s\n' "$TOKEN" >> "$ROOT/.env"
    log "ACTION_TOKEN dibuat dan disimpan ke .env"
  else
    log "PERINGATAN: gagal membuat ACTION_TOKEN"
  fi
elif [ ! -f "$ROOT/.env" ]; then
  log "PERINGATAN: .env belum ada — salin dari .env.example lalu isi kuncinya"
fi

PYTHONPATH="$ROOT:$PYTHON_USER_SITE${PYTHONPATH:+:$PYTHONPATH}" "$PY" - <<'PYTEST' || fail "self-test tool gagal (lihat pesan di atas)"
from tools import registry
registry.discover()
required = {"cron", "owner_ops", "plugin_control", "group_summary",
            "shell", "terminal", "files", "browser", "web_fetch", "vision", "skill"}
loaded = {tool["name"] for tool in registry.all_tools()}
missing = sorted(required - loaded)
if missing:
    raise SystemExit("tools wajib gagal dimuat: " + ", ".join(missing))

from agent.config import CONFIG
sec = (CONFIG.get("security") or {})
if not sec:
    raise SystemExit("security kosong di config — tool shell akan menolak semua perintah")
from tools.builtin.shell import handler as shell_run
res = shell_run({"command": "echo kwee-ok"}, dict(CONFIG))
if (res.get("stdout") or "").strip() != "kwee-ok":
    raise SystemExit(f"tool shell TIDAK bisa menjalankan perintah: {res}")
# `terminal` = shell + cwd yang bertahan. Diuji terpisah karena perintah
# bertahap (cd lalu baca berkas) adalah pola kerja utama agent.
from tools.builtin.terminal import handler as term_run
_tcfg = dict(CONFIG)
_tcfg["context"] = {"chat_id": "selftest"}
_tres = term_run({"command": "echo kwee-term-ok"}, _tcfg)
if "kwee-term-ok" not in (_tres.get("stdout") or ""):
    raise SystemExit(f"tool terminal TIDAK bisa menjalankan perintah: {_tres}")
print(f"[setup] registry + tool shell OK ({len(loaded)} tool; "
      f"allowAllExecutables={bool(sec.get('allowAllExecutables'))})")
PYTEST

log "laporan kemampuan:"
HAVE=""
for bin in curl wget git jq ffmpeg ffprobe yt-dlp unzip tar node npm agent-canvas uvx rtk; do
  if command -v "$bin" >/dev/null 2>&1; then HAVE="$HAVE $bin"; else log "  TIDAK ADA: $bin"; fi
done
log "  tersedia:$HAVE"
log "selesai — jalankan ./start.sh"
