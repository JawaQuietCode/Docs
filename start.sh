#!/usr/bin/env bash
# start.sh — jalankan kweewind blows (inti agent Python + transport WhatsApp Node).
#
# Perubahan penting:
#   * uvicorn dijalankan dengan `--workers 1` (eksplisit): lock per-chat,
#     peta aktivitas, dan watchdog job hidup di MEMORI PROSES. Dengan >1 worker
#     serialisasi chat dan deteksi job macet tidak bekerja.
#   * ACTION_TOKEN dipastikan ada. Token yang sama dipakai transport ↔ inti
#     agent (header X-Agent-Token), dan health check ikut mengirimnya.
#   * ada self-check kemampuan (curl/git/jq/ffmpeg/yt-dlp/playwright) dan
#     self-test tool (shell benar-benar menjalankan `echo`), supaya masalah
#     "bot tidak bisa apa-apa" kelihatan saat start, bukan saat dipakai.
set -Eeuo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"
export HOME="$ROOT"
export TMPDIR="$ROOT/.tmp"
export PYTHONUSERBASE="$ROOT/.pyuser"
export PIP_USER=1
export PIP_DISABLE_PIP_VERSION_CHECK=1
export PIP_BREAK_SYSTEM_PACKAGES=1
export PYTHONDONTWRITEBYTECODE=1
export PYTHONUNBUFFERED=1
export KWEE_ROOT="$ROOT"
export PYTHONPATH="$ROOT${PYTHONPATH:+:$PYTHONPATH}"
export TZ="${TZ:-Asia/Jakarta}"
mkdir -p "$TMPDIR" "$PYTHONUSERBASE" "$ROOT/data/media/files" "$ROOT/data/inbox" "$ROOT/data/browser"
fail() { printf '[start] ERROR: %s\n' "$*" >&2; exit 1; }
log() { printf '[start] %s\n' "$*"; }

# First boot creates a private local config; existing .env is never overwritten.
if [ ! -f "$ROOT/.env" ]; then
  [ -f "$ROOT/.env.example" ] || fail ".env dan .env.example tidak ditemukan. Pulihkan template konfigurasi proyek."
  cp "$ROOT/.env.example" "$ROOT/.env"
  log "template .env dibuat; konfigurasi pertama akan dipandu di terminal"
fi
chmod 600 "$ROOT/.env" 2>/dev/null || true

if [ -f "$ROOT/.env" ]; then
  while IFS='=' read -r key value || [ -n "${key:-}" ]; do
    case "${key:-}" in ''|\#*) continue ;; esac
    key="$(printf '%s' "$key" | tr -d ' \t\r')"
    [ -n "$key" ] || continue
    value="$(printf '%s' "${value:-}" | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//' -e 's/^"//' -e 's/"$//' -e "s/^'//" -e "s/'$//" | tr -d '\r')"
    if [ -z "${!key+x}" ]; then export "$key=$value"; fi
  done < "$ROOT/.env"
fi

# RTK native rewrite follows .env loading so KWEE_RTK=0 is respected on every
# standard startup path (not only during setup.sh). The helper is idempotent;
# if RTK is unavailable, the original Kwee shell path remains active.
. "$ROOT/scripts/setup_rtk.sh"
kwee_setup_rtk

PY="${PY:-python3}"
if ! command -v "$PY" >/dev/null 2>&1; then
  # Image Pterodactyl biasanya sudah punya python3. Kalau tidak, coba pasang
  # (hanya mungkin bila proses ini root), lalu beri pesan yang bisa ditindak.
  if [ "$(id -u)" = "0" ] && command -v apt-get >/dev/null 2>&1; then
    log "Python tidak ditemukan; mencoba memasang python3 + pip"
    apt-get update -qq >"$TMPDIR/apt.log" 2>&1 || true
    apt-get install -y -qq python3 python3-pip python3-venv >>"$TMPDIR/apt.log" 2>&1 || true
  fi
fi
command -v "$PY" >/dev/null 2>&1 || fail "Python 3 tidak ditemukan. Pasang python3 + pip di image (egg Pterodactyl: 'Python 3.11' / 'Generic Node.js' + python3), lalu start ulang."
PY="$(command -v "$PY")"
# Interpreter INI yang dipakai transport Node (plugins/spdl.js, tourl.js,
# backup-full.js). Tanpa ini Node memanggil /usr/bin/python3 dan modul yang
# dipasang ke user site proyek tidak terlihat ("No module named yt_dlp").
export KWEE_PY="$PY"
# Dua lokasi user site: milik PYTHONUSERBASE proyek DAN ~/.local bawaan.
# Dulu hanya PYTHONUSERBASE yang masuk PYTHONPATH, sehingga paket yang sudah
# terpasang di ~/.local (mis. yt-dlp dari setup.sh) jadi TIDAK terlihat.
PYTHON_USER_SITE="$("$PY" -c 'import site; print(site.getusersitepackages())' 2>/dev/null || true)"
DEFAULT_USER_SITE="$(env -u PYTHONUSERBASE "$PY" -c 'import site; print(site.getusersitepackages())' 2>/dev/null || true)"
for _site in "$PYTHON_USER_SITE" "$DEFAULT_USER_SITE"; do
  [ -n "$_site" ] && case ":$PYTHONPATH:" in *":$_site:"*) ;; *) export PYTHONPATH="$ROOT:$_site${PYTHONPATH:+:$PYTHONPATH}" ;; esac
done
command -v node >/dev/null 2>&1 || fail "Node.js tidak ditemukan. Gunakan image Pterodactyl yang menyediakan Node.js versi 20 atau lebih baru."
command -v npm >/dev/null 2>&1 || fail "npm tidak ditemukan."
[ -f "$ROOT/agent/main.py" ] || fail "File agent/main.py tidak ditemukan di $ROOT. Pastikan folder agent sudah diunggah."
[ -f "$ROOT/transport/wa.js" ] || fail "File transport/wa.js tidak ditemukan di $ROOT. Pastikan folder transport sudah diunggah."

# --------------------------------------------------------------------------- #
# TOKEN AGENT (transport ↔ inti agent)
# Tanpa token, /message bisa dipanggil proses lain di localhost (atau oleh agent
# sendiri lewat `curl localhost:8787/message`) dan bot bertindak sebagai owner.
# Kalau .env belum punya ACTION_TOKEN, buat sekali di sini dan tulis ke .env
# supaya transport Node membaca nilai yang sama.
# --------------------------------------------------------------------------- #
if [ -z "${ACTION_TOKEN:-}" ]; then
  ACTION_TOKEN="$(node -e "console.log(require('crypto').randomBytes(24).toString('hex'))" 2>/dev/null || true)"
  if [ -n "$ACTION_TOKEN" ]; then
    printf '%s' "$ACTION_TOKEN" | "$PY" "$ROOT/scripts/bootstrap_config.py" --env "$ROOT/.env" --persist-action-token \
      || fail "Gagal menyimpan ACTION_TOKEN ke .env secara atomik"
    export ACTION_TOKEN
    log "ACTION_TOKEN belum ada; dibuat otomatis dan disimpan ke .env"
  else
    log "PERINGATAN: gagal membuat ACTION_TOKEN — endpoint agent berjalan TANPA autentikasi"
  fi
fi

PORT="${AGENT_PORT:-8787}"
HOST="${AGENT_HOST:-127.0.0.1}"
if [ "$HOST" != "127.0.0.1" ] && [ "$HOST" != "localhost" ]; then
  log "AGENT_HOST=$HOST tidak aman untuk core; menggunakan 127.0.0.1"
  HOST="127.0.0.1"
fi
if [[ "${SERVER_PORT:-}" =~ ^[0-9]+$ ]] && [ "$PORT" = "$SERVER_PORT" ]; then
  log "AGENT_PORT sama dengan port publik; mengganti ke 8787"
  PORT=8787
fi
export AGENT_HOST="$HOST"
export AGENT_PORT="$PORT"
log "folder proyek: $ROOT"
log "HOME: $HOME"
log "python: $("$PY" --version 2>&1)"
log "node: $(node --version 2>&1)"
log "npm: $(npm --version 2>&1)"
log "agent bind: $HOST:$PORT"

# Dependensi Node inti ada di package.json (Baileys, boom, pino, playwright), jadi
# SATU `npm install` cukup dan tidak ada paket yang "dipangkas" install berikutnya.
node_deps_ok() {
  node --input-type=module -e "await import('@itsliaaa/baileys'); await import('@hapi/boom'); await import('pino')" >/dev/null 2>&1
}
pasang_node_deps() {
  npm install --no-package-lock --no-audit --no-fund >"$TMPDIR/npm-start.log" 2>&1
}
if ! node_deps_ok; then
  log "Paket Node inti tidak ditemukan; npm install (Baileys, @hapi/boom, pino, playwright)"
  if ! pasang_node_deps; then
    tail -50 "$TMPDIR/npm-start.log" >&2 || true
    fail "Instalasi paket Node gagal. Periksa koneksi internet/registry npm dan log $TMPDIR/npm-start.log."
  fi
fi
node_deps_ok || fail "Paket Node inti tetap tidak dapat diimpor setelah instalasi. Lihat $TMPDIR/npm-start.log."
log "modul Node inti berhasil dimuat"

if ! "$PY" -m pip --version >/dev/null 2>&1; then
  log "pip belum tersedia; mencoba mengaktifkan ensurepip"
  "$PY" -m ensurepip --upgrade --user >"$TMPDIR/ensurepip.log" 2>&1 || true
fi
if ! "$PY" -m pip --version >/dev/null 2>&1; then
  command -v curl >/dev/null 2>&1 || fail "pip dan ensurepip tidak tersedia, dan curl tidak ditemukan untuk bootstrap pip."
  log "pip masih belum tersedia; mengambil bootstrap pip"
  curl -fsSL --max-time 90 https://bootstrap.pypa.io/get-pip.py -o "$TMPDIR/get-pip.py" \
    || fail "Gagal mengunduh get-pip.py. Periksa koneksi internet."
  "$PY" "$TMPDIR/get-pip.py" --user --break-system-packages >"$TMPDIR/get-pip.log" 2>&1 \
    || { tail -50 "$TMPDIR/get-pip.log" >&2 || true; fail "Bootstrap pip gagal."; }
fi
if ! "$PY" -c 'import fastapi, uvicorn, httpx, yaml' >/dev/null 2>&1; then
  log "Dependensi Python belum lengkap; memasang FastAPI, Uvicorn, HTTPX, PyYAML, dan Pillow"
  if ! "$PY" -m pip install --user --break-system-packages \
    "fastapi>=0.115.0" "uvicorn>=0.30.6" httpx==0.27.2 pyyaml==6.0.2 pillow==10.4.0 \
    >"$TMPDIR/pip-start.log" 2>&1; then
    tail -50 "$TMPDIR/pip-start.log" >&2 || true
    fail "Instalasi dependensi Python gagal. Periksa koneksi internet dan log $TMPDIR/pip-start.log."
  fi
fi
"$PY" -c 'import fastapi, uvicorn, httpx, yaml; print("[start] modul Python inti berhasil dimuat")' \
  || fail "Modul Python inti tidak dapat diimpor setelah instalasi."

# One-time guided setup. The helper emits only shell-quoted required exports;
# prompts and diagnostics go to stderr so `eval` can never execute log text.
[ -f "$ROOT/scripts/bootstrap_config.py" ] || fail "scripts/bootstrap_config.py tidak ditemukan."
BOOTSTRAP_EXPORTS="$("$PY" "$ROOT/scripts/bootstrap_config.py" --env "$ROOT/.env" --exports)" \
  || fail "Konfigurasi awal belum lengkap/valid. Jalankan kembali bash start.sh di terminal interaktif."
eval "$BOOTSTRAP_EXPORTS"
chmod 600 "$ROOT/.env" 2>/dev/null || true
log "konfigurasi wajib terverifikasi; rahasia tersimpan lokal dengan permission 0600"

# Pillow dipakai agent/llm/client.py untuk memperkecil gambar (vision).
"$PY" -c 'import PIL; print("[start] Pillow tersedia:", PIL.__version__)' \
  || log "Pillow tidak tersedia; gambar vision dikirim apa adanya (bisa ditolak gateway)"

# --------------------------------------------------------------------------- #
# PERKAKAS SISTEM: ffmpeg/ffprobe dipakai untuk konversi audio (plugin spdl) dan
# probe durasi. Hanya dicoba bila proses ini root — di Pterodactyl biasanya
# tidak, dan itu tidak masalah (cukup dilaporkan di capability report).
# --------------------------------------------------------------------------- #
if ! command -v ffmpeg >/dev/null 2>&1 && [ "$(id -u)" = "0" ] && command -v apt-get >/dev/null 2>&1; then
  log "ffmpeg belum ada; mencoba memasang (best-effort)"
  timeout 180 apt-get install -y -qq ffmpeg >"$TMPDIR/apt-ffmpeg.log" 2>&1 \
    && log "ffmpeg terpasang" \
    || log "PERINGATAN: ffmpeg gagal dipasang — konversi audio tidak akan bekerja"
fi

# --------------------------------------------------------------------------- #
# PERKAKAS CLI PYTHON: yt-dlp (unduh media) dan paket lain yang dipanggil agent
# lewat baris perintah. Dulu hanya setup.sh yang memasang yt-dlp, jadi pada
# instalasi yang cuma menjalankan start.sh gejalanya:
#   "yt-dlp: /usr/bin/python3: No module named yt_dlp"
# Sekarang start.sh memeriksa dan memasangnya sendiri (best-effort, tidak
# mematikan bot bila gagal — hanya tool terkait yang tidak tersedia).
# --------------------------------------------------------------------------- #
if ! "$PY" -c 'import yt_dlp' >/dev/null 2>&1; then
  log "yt-dlp belum ada di $PY; memasang (unduh media/audio)"
  "$PY" -m pip install --user --break-system-packages --upgrade yt-dlp \
    >"$TMPDIR/pip-ytdlp.log" 2>&1 \
    && log "yt-dlp terpasang: $("$PY" -m yt_dlp --version 2>/dev/null | head -1)" \
    || { tail -20 "$TMPDIR/pip-ytdlp.log" >&2 || true; log "PERINGATAN: yt-dlp gagal dipasang — !spdl / unduh audio tidak akan bekerja"; }
fi
# `yt-dlp` sebagai binary di PATH. Kalau hanya modul Python yang ada (kasus di
# panel: `command -v yt-dlp` = kosong), buat shim supaya shell agent tetap bisa
# memanggilnya — inilah yang dulu bikin bot bilang "yt-dlp tidak ada".
mkdir -p "$HOME/.local/bin"
if ! command -v yt-dlp >/dev/null 2>&1 && "$PY" -c 'import yt_dlp' >/dev/null 2>&1; then
  printf '#!/bin/sh\nexec "%s" -m yt_dlp "$@"\n' "$PY" > "$HOME/.local/bin/yt-dlp"
  chmod +x "$HOME/.local/bin/yt-dlp"
  log "shim yt-dlp dibuat: $HOME/.local/bin/yt-dlp"
fi
case ":$PATH:" in *":$HOME/.local/bin:"*) ;; *) export PATH="$HOME/.local/bin:$PATH" ;; esac
# `kwee` harus bisa dipanggil dari shell mana pun — termasuk bash milik engine
# OpenHands, yang tidak mewarisi PATH interaktif kita.
if [ -x "$ROOT/bin/kwee" ]; then
  ln -sf "$ROOT/bin/kwee" "$HOME/.local/bin/kwee" 2>/dev/null || true
  log "CLI kwee siap: $HOME/.local/bin/kwee"
fi
# curl_cffi dipakai yt-dlp untuk `--impersonate` (TikTok/IG sering menolak UA
# biasa). Best-effort: tanpa ini unduhan tetap dicoba.
"$PY" -c 'import curl_cffi' >/dev/null 2>&1 \
  || "$PY" -m pip install --user --break-system-packages --quiet curl_cffi >"$TMPDIR/pip-cffi.log" 2>&1 \
  || log "PERINGATAN: curl_cffi gagal dipasang (impersonate browser tidak tersedia)"
if command -v yt-dlp >/dev/null 2>&1; then
  log "yt-dlp siap: $(yt-dlp --version 2>/dev/null | head -1)"
else
  log "PERINGATAN: yt-dlp tidak tersedia — tool download akan mencoba memasang sendiri saat dipakai"
fi

# Intelligence integrations are installed once (missing packages only). Their
# external services remain optional; core native chat never depends on Docker,
# GitHub credentials, Qdrant, or a remote Graphify workspace.
if [ "${KWEE_INSTALL_INTELLIGENCE:-1}" != "0" ] && [ -f "$ROOT/setup-intelligence.sh" ]; then
  log "memastikan dependensi intelligence tersedia (instalasi hanya saat pertama kali)"
  if ! PY="$PY" bash "$ROOT/setup-intelligence.sh" >"$TMPDIR/intelligence-install.log" 2>&1; then
    tail -30 "$TMPDIR/intelligence-install.log" >&2 || true
    log "PERINGATAN: integrasi intelligence tidak lengkap; native chat tetap dilanjutkan"
  fi
  export PATH="$PYTHONUSERBASE/bin:$HOME/.local/bin:$PATH"
  if [ "${KWEE_BUILD_GRAPH:-1}" != "0" ] && [ ! -s "$ROOT/graphify-out/graph.json" ] && command -v graphify >/dev/null 2>&1; then
    log "membangun Graphify code graph lokal (pertama kali)"
    if ! bash "$ROOT/scripts/build-project-graph.sh" >"$TMPDIR/graphify-start.log" 2>&1; then
      tail -20 "$TMPDIR/graphify-start.log" >&2 || true
      log "PERINGATAN: Graphify graph belum siap; chat native tetap berjalan"
    fi
  fi
fi
if [[ "${KWEE_MEM0_ENABLED:-false}" =~ ^(1|true|yes|on)$ ]]; then
  if command -v docker >/dev/null 2>&1 && docker compose version >/dev/null 2>&1; then
    log "Mem0 aktif: memastikan Qdrant lokal berjalan"
    docker compose -f "$ROOT/docker-compose.intelligence.yml" up -d \
      || log "PERINGATAN: Qdrant tidak menyala; memori lokal FTS5 tetap tersedia"
  else
    log "PERINGATAN: Mem0 diaktifkan tetapi Docker Compose tidak tersedia; memori lokal FTS5 tetap tersedia"
  fi
fi
if [ -n "${GITHUB_PERSONAL_ACCESS_TOKEN:-}" ] && ! command -v docker >/dev/null 2>&1; then
  log "PERINGATAN: GitHub MCP token tersedia tetapi Docker tidak ada; MCP GitHub dilewati"
fi

# --------------------------------------------------------------------------- #
# ENGINE OPENHANDS (@openhands/agent-canvas) — OPSIONAL
#
# Tiga hal yang membuat pemasangan gagal di panel (dan ditangani di sini):
#   1. Node image < 24 → paket menolak jalan. Node 24 diunduh ke $ROOT/.node.
#   2. `npm install -g` ke prefix sistem tidak boleh ditulis user container →
#      dipasang ke $ROOT/.npm-global (NPM_CONFIG_PREFIX), lalu PATH ditambah.
#   3. `uv` tidak ada → agent-server (uvx) tidak bisa dijalankan. Dicoba lewat
#      pip dulu, baru installer curl.
# Semua kegagalan hanya mematikan engine canvas; bot tetap jalan (`native`).
# --------------------------------------------------------------------------- #
CANVAS_ON="${KWEE_CANVAS:-0}"
for arg in "$@"; do
  [ "$arg" = "--canvas" ] && CANVAS_ON=1
  [ "$arg" = "--no-canvas" ] && CANVAS_ON=0
done
case "$CANVAS_ON" in
  0|false|off|no) CANVAS_ON=0 ;;
  *) CANVAS_ON=1 ;;
esac

NODE_BUTUH=24
NODE_VER="v24.9.0"
NPM_PREFIX="$ROOT/.npm-global"
export NPM_CONFIG_PREFIX="${NPM_CONFIG_PREFIX:-$NPM_PREFIX}"
export PATH="$NPM_PREFIX/bin:$ROOT/.node/bin:$PYTHONUSERBASE/bin:$HOME/.local/bin:$PATH"

# Node >= 24: pakai yang ada, atau unduh yang resmi ke dalam proyek.
siapkan_node() {
  local ada major arch url
  ada="$(command -v node 2>/dev/null || true)"
  if [ -n "$ada" ]; then
    major="$("$ada" -p 'process.versions.node.split(".")[0]' 2>/dev/null || echo 0)"
    if [ "${major:-0}" -ge "$NODE_BUTUH" ]; then
      log "node $("$ada" -v) (>= $NODE_BUTUH) — memadai"
      return 0
    fi
    log "node $("$ada" -v) < $NODE_BUTUH; menyiapkan Node $NODE_VER di $ROOT/.node"
  else
    log "node tidak ditemukan; menyiapkan Node $NODE_VER di $ROOT/.node"
  fi
  if [ ! -x "$ROOT/.node/bin/node" ]; then
    case "$(uname -m)" in
      x86_64|amd64) arch=x64 ;;
      aarch64|arm64) arch=arm64 ;;
      *) log "PERINGATAN: arsitektur $(uname -m) tidak punya unduhan Node resmi"; return 1 ;;
    esac
    url="https://nodejs.org/dist/$NODE_VER/node-$NODE_VER-linux-$arch.tar.gz"
    mkdir -p "$ROOT/.node"
    if command -v curl >/dev/null 2>&1; then
      curl -fL --max-time 300 "$url" -o "$TMPDIR/node.tar.gz" >"$TMPDIR/node-unduh.log" 2>&1 || { tail -5 "$TMPDIR/node-unduh.log" >&2 || true; return 1; }
    elif command -v wget >/dev/null 2>&1; then
      wget -q -T 300 -O "$TMPDIR/node.tar.gz" "$url" || return 1
    else
      log "PERINGATAN: curl dan wget tidak ada; Node $NODE_BUTUH tidak bisa disiapkan"
      return 1
    fi
    tar -xzf "$TMPDIR/node.tar.gz" -C "$ROOT/.node" --strip-components=1 || return 1
    rm -f "$TMPDIR/node.tar.gz"
  fi
  [ -x "$ROOT/.node/bin/node" ] || return 1
  export PATH="$ROOT/.node/bin:$PATH"
  log "node lokal siap: $("$ROOT/.node/bin/node" -v)"
  return 0
}

# uv/uvx: pip lebih dulu (tidak butuh curl ke shell), installer resmi sebagai cadangan.
siapkan_uv() {
  command -v uvx >/dev/null 2>&1 && { log "uvx $("uvx" --version 2>/dev/null | head -1) — memadai"; return 0; }
  log "uv/uvx belum ada; memasang lewat pip"
  "$PY" -m pip install --user --break-system-packages --upgrade uv >"$TMPDIR/pip-uv.log" 2>&1 \
    && { export PATH="$PYTHONUSERBASE/bin:$HOME/.local/bin:$PATH"; command -v uvx >/dev/null 2>&1 && { log "uv terpasang (pip): $(uvx --version 2>/dev/null | head -1)"; return 0; }; }
  tail -10 "$TMPDIR/pip-uv.log" >&2 2>/dev/null || true
  if command -v curl >/dev/null 2>&1; then
    log "mencoba installer resmi uv"
    curl -LsSf --max-time 180 https://astral.sh/uv/install.sh | sh >"$TMPDIR/uv-install.log" 2>&1 \
      && { export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"; command -v uvx >/dev/null 2>&1 && { log "uv terpasang (installer)"; return 0; }; }
    tail -10 "$TMPDIR/uv-install.log" >&2 2>/dev/null || true
  fi
  log "PERINGATAN: uv/uvx tidak tersedia — agent-server tidak bisa dijalankan"
  return 1
}

CANVAS_PORT="$("$PY" -c "
import yaml
c = yaml.safe_load(open('$ROOT/config/default.yaml', encoding='utf-8')) or {}
print(((c.get('mechanisms') or {}).get('canvas') or {}).get('port') or 8000)
" 2>/dev/null || echo 8000)"
if [ "$CANVAS_PORT" = "${SERVER_PORT:-}" ] || [ "$CANVAS_PORT" = "$PORT" ]; then
  log "PERINGATAN: port canvas ($CANVAS_PORT) sama dengan port lain yang dipakai; ubah mechanisms.canvas.port di config/default.yaml"
fi
PREWARM_PID=""

if [ "$CANVAS_ON" = "1" ]; then
  log "engine OpenHands: memeriksa Node, agent-canvas, dan uv"
  if siapkan_node; then
    if ! command -v agent-canvas >/dev/null 2>&1; then
      log "memasang @openhands/agent-canvas ke $NPM_PREFIX (bisa lama)"
      if npm install -g --prefix "$NPM_PREFIX" --no-fund --no-audit @openhands/agent-canvas \
           >"$TMPDIR/npm-canvas.log" 2>&1; then
        log "agent-canvas terpasang: $(agent-canvas --version 2>/dev/null | head -1)"
      else
        log "pemasangan dengan --prefix gagal; mencoba npm global biasa"
        if npm install -g --no-fund --no-audit @openhands/agent-canvas >"$TMPDIR/npm-canvas2.log" 2>&1; then
          log "agent-canvas terpasang: $(agent-canvas --version 2>/dev/null | head -1)"
        else
          tail -25 "$TMPDIR/npm-canvas.log" >&2 2>/dev/null || true
          tail -15 "$TMPDIR/npm-canvas2.log" >&2 2>/dev/null || true
          log "PERINGATAN: agent-canvas gagal dipasang — engine canvas tidak tersedia (bot memakai native)"
        fi
      fi
    else
      log "agent-canvas sudah ada: $(agent-canvas --version 2>/dev/null | head -1)"
    fi
  else
    log "PERINGATAN: Node $NODE_BUTUH tidak siap — agent-canvas dilewati"
  fi
  siapkan_uv || true

  # Ringkasan jujur: apa yang benar-benar siap dipakai.
  if command -v agent-canvas >/dev/null 2>&1; then
    log "engine canvas : SIAP  ($(command -v agent-canvas))"
  else
    log "engine canvas : TIDAK ADA — bot memakai engine native"
  fi
  if command -v uvx >/dev/null 2>&1; then
    log "agent-server  : uvx $(command -v uvx)"
  else
    log "agent-server  : uvx TIDAK ADA — stack canvas tidak bisa menyala"
  fi

  # PREWARM: nyalakan stack di latar supaya pesan pertama tidak menunggu uvx
  # mengunduh agent-server. Matikan dengan KWEE_CANVAS_PREWARM=0.
  if command -v agent-canvas >/dev/null 2>&1 && command -v uvx >/dev/null 2>&1 \
     && [ "${KWEE_CANVAS_PREWARM:-1}" != "0" ]; then
    log "memanaskan engine canvas di latar (log: data/canvas.log)"
    # PID dicatat supaya cleanup bisa menghentikannya (dulu jadi proses yatim).
    "$PY" -m agent.canvas_engine start >"$TMPDIR/canvas-prewarm.log" 2>&1 &
    PREWARM_PID=$!
  fi
  # PATH bisa berpindah ke Node 24 lokal di atas: pastikan modul inti tetap termuat.
  node_deps_ok || { log "Node berganti versi; memasang ulang paket Node"; pasang_node_deps || log "PERINGATAN: pasang ulang paket Node gagal ($TMPDIR/npm-start.log)"; }
fi

if [ ! -f "$ROOT/scheduler/cron.py" ]; then
  fail "Modul proyek scheduler/cron.py tidak ditemukan. Unggah folder scheduler dari proyek asli ke $ROOT/scheduler; start.sh tidak dapat membuat source modul yang hilang."
fi
if [ ! -f "$ROOT/scheduler/__init__.py" ]; then
  printf '%s\n' '"""Local Kwee scheduler package."""' > "$ROOT/scheduler/__init__.py"
fi
PYTHONPATH="$ROOT${PYTHONPATH:+:$PYTHONPATH}" "$PY" -c 'from scheduler import cron; print("[start] modul scheduler.cron berhasil dimuat")' \
  || fail "scheduler/cron.py ada tetapi gagal diimpor. Periksa error Python di atas dan dependensi modul scheduler."

# --------------------------------------------------------------------------- #
# FREE CLAUDE CODE CLI — best-effort, pinned upstream install.
# FCC requires Python 3.14.10 and its own provider config; this CLI is not
# claimed to share Kwee's env/model. Native `subagent` remains same-provider.
# --------------------------------------------------------------------------- #
FCC_COMMIT="da29b69db4cf4854c6d6fb4195e5628db04c219d"
if command -v fcc-server >/dev/null 2>&1; then
  log "Free Claude Code CLI tersedia: $(command -v fcc-server)"
else
  if ! command -v uv >/dev/null 2>&1; then
    log "menyiapkan uv untuk Free Claude Code CLI"
    siapkan_uv || true
  fi
  if command -v uv >/dev/null 2>&1; then
  log "memasang Free Claude Code CLI (Python 3.14.10; instalasi best-effort)"
  if uv tool install --python 3.14.10 \
    "git+https://github.com/alishahryar1/free-claude-code.git@${FCC_COMMIT}" \
    >"$TMPDIR/fcc-install.log" 2>&1; then
    export PATH="$(uv tool dir --bin 2>/dev/null):$PATH"
    log "Free Claude Code CLI terpasang; konfigurasi provider dikelola FCC"
  else
    tail -20 "$TMPDIR/fcc-install.log" >&2 || true
    log "PERINGATAN: Free Claude Code CLI tidak terpasang; Kwee native peers tetap tersedia"
  fi
  else
    log "PERINGATAN: uv tidak tersedia; lewati pemasangan Free Claude Code CLI. Kwee native peers tetap tersedia"
  fi
fi
# SELF-TEST TOOL: registry harus memuat tool inti DAN `shell` harus benar-benar
# bisa menjalankan perintah. Ini menangkap "allowlist kosong" / "config tidak
# terbaca" saat start, bukan saat owner memakai bot.
# --------------------------------------------------------------------------- #
PYTHONPATH="$ROOT:$PYTHON_USER_SITE${PYTHONPATH:+:$PYTHONPATH}" "$PY" - <<'PYTEST' || fail "self-test tool gagal (lihat pesan di atas)"
from tools import registry
registry.discover()
required = {"cron", "owner_ops", "plugin_control", "group_summary",
            "shell", "terminal", "files", "browser", "web_fetch", "vision", "skill",
            "search_all", "group_admin", "subagent", "download", "web_see"}
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
print("[start] registry + tool shell/terminal OK "
      f"({len(loaded)} tool; allowAllExecutables={bool(sec.get('allowAllExecutables'))})")
PYTEST

# --------------------------------------------------------------------------- #
# MEMORI AWAL: isi kwee.db + MEMORY.md sekali, hanya kalau masih kosong.
# Tujuannya supaya instalasi baru tidak bertanya hal yang sudah pasti (peta
# folder, perintah, engine). Tuning manual TIDAK pernah ditimpa: seed berhenti
# begitu ada entri. Matikan dengan KWEE_SEED_MEMORY=0, paksa ulang dengan
# `python3 -m memory.seed --reset`.
# --------------------------------------------------------------------------- #
if [ "${KWEE_SEED_MEMORY:-1}" != "0" ]; then
  if PYTHONPATH="$ROOT:$PYTHON_USER_SITE${PYTHONPATH:+:$PYTHONPATH}" \
     "$PY" -m memory.seed --if-empty --learn >"$TMPDIR/seed-memory.log" 2>&1; then
    log "memori awal: $(tr -d '\n' < "$TMPDIR/seed-memory.log" | head -c 200)"
  else
    log "PERINGATAN: seed memori gagal; lihat $TMPDIR/seed-memory.log"
  fi
fi

if [ -d "$ROOT/.venv" ] && [ ! -x "$ROOT/.venv/bin/python" ]; then
  log "Menghapus virtualenv tidak valid dari image sebelumnya"
  rm -rf "$ROOT/.venv"
fi

playwright_ok() { node --input-type=module -e "await import('playwright')" >/dev/null 2>&1; }
if ! playwright_ok; then
  log "Playwright tidak tersedia; npm install untuk tool browser"
  pasang_node_deps || { tail -30 "$TMPDIR/npm-start.log" >&2 || true; log "Playwright gagal dipasang; fungsi browser tidak tersedia, bot tetap dilanjutkan"; }
fi
if playwright_ok; then
  # Cache dibuat eksplisit di root proyek karena HOME diarahkan ke folder proyek.
  export PLAYWRIGHT_BROWSERS_PATH="${PLAYWRIGHT_BROWSERS_PATH:-$ROOT/.cache/ms-playwright}"
  mkdir -p "$PLAYWRIGHT_BROWSERS_PATH"
  if ! node --input-type=module -e 'import { chromium } from "playwright"; const b=await chromium.launch({headless:true}); await b.close()' >/dev/null 2>&1; then
    log "Chromium belum dapat dijalankan; memasang browser dan dependensi sistem"
    # Playwright install-deps memerlukan root. Di panel non-root tetap unduh
    # browser dan laporkan dengan jelas bila image belum menyertakan shared libs.
    if [ "$(id -u)" = "0" ]; then
      npx --no-install playwright install-deps chromium >"$TMPDIR/playwright-deps.log" 2>&1 \
        || log "PERINGATAN: dependensi sistem Chromium tidak lengkap (lihat $TMPDIR/playwright-deps.log)"
    fi
    npx --no-install playwright install chromium >"$TMPDIR/playwright-chromium.log" 2>&1 \
      || { tail -30 "$TMPDIR/playwright-chromium.log" >&2 || true; log "PERINGATAN: Chromium gagal diunduh"; }
  else
    log "Chromium Playwright siap (launch smoke-test lulus)"
  fi
  if ! node --input-type=module -e 'import { chromium } from "playwright"; const b=await chromium.launch({headless:true}); await b.close()' >/dev/null 2>&1; then
    log "PERINGATAN: Chromium masih gagal diluncurkan; tool browser tidak tersedia pada image ini"
  fi
fi

MEDIA_BIN="$HOME/.local/bin"
mkdir -p "$MEDIA_BIN"
case ":$PATH:" in *":$MEDIA_BIN:"*) ;; *) export PATH="$MEDIA_BIN:$PATH" ;; esac
case ":$PATH:" in *":$PYTHONUSERBASE/bin:"*) ;; *) export PATH="$PYTHONUSERBASE/bin:$PATH" ;; esac

# --------------------------------------------------------------------------- #
# SELF-CHECK KEMAMPUAN: apa saja yang benar-benar bisa dipakai agent
# --------------------------------------------------------------------------- #
capability_report() {
  local have="" missing=""
  for bin in curl wget git jq ffmpeg ffprobe yt-dlp unzip tar rtk; do
    if command -v "$bin" >/dev/null 2>&1; then have="$have $bin"; else missing="$missing $bin"; fi
  done
  # yt-dlp sering HANYA ada sebagai modul Python (bukan binary di PATH).
  if ! command -v yt-dlp >/dev/null 2>&1 && "$PY" -c 'import yt_dlp' >/dev/null 2>&1; then
    have="$have yt-dlp(modul)"; missing="${missing/ yt-dlp/}"
  fi
  log "kemampuan tersedia:$have"
  [ -n "$missing" ] && log "kemampuan TIDAK ada (tool terkait akan gagal):$missing"
  if node --input-type=module -e 'import { chromium } from "playwright"; const b=await chromium.launch({headless:true}); await b.close()' >/dev/null 2>&1; then
    log "browser: Playwright + Chromium launch berhasil"
  else
    log "browser: Chromium tidak bisa launch (cek $TMPDIR/playwright-chromium.log dan shared libraries image)"
  fi
}

if [ -f "$ROOT/engines.sh" ] && [ "${KWEE_INSTALL_ENGINES:-0}" != "0" ]; then
  log "Memasang engine opsional karena KWEE_INSTALL_ENGINES aktif"
  bash "$ROOT/engines.sh" >"$TMPDIR/kwee-engines.log" 2>&1 &
fi
if [ -f "$ROOT/selftest_memory.py" ]; then
  log "Menjalankan self-test memori"
  "$PY" "$ROOT/selftest_memory.py" >"$TMPDIR/selftest-memory.log" 2>&1 \
    || log "Self-test memori gagal; lihat $TMPDIR/selftest-memory.log"
fi
capability_report

# --------------------------------------------------------------------------- #
# PENJAGA WEB STACK: paket intelligence (mcp, graphifyy, langchain) bisa menarik
# Starlette naik sehingga `fastapi` lama gagal dibuat (TypeError di
# APIRouter.__init__ → agent mati sebelum health check). Diperiksa dengan
# benar-benar membuat FastAPI() dan diperbaiki otomatis bila perlu.
# --------------------------------------------------------------------------- #
if [ -f "$ROOT/scripts/ensure_web_stack.py" ]; then
  "$PY" "$ROOT/scripts/ensure_web_stack.py" \
    || fail "FastAPI/Starlette tidak cocok dan gagal diperbaiki otomatis. Lihat pesan [web-stack] di atas."
fi

log "Memulai agent Python pada $HOST:$PORT"
# --workers 1 WAJIB: lease lock per-chat, peta aktivitas, dan watchdog job hidup
# di memori proses. Lebih dari satu worker = serialisasi chat dan deteksi job
# macet tidak berfungsi (permintaan bisa nyasar ke worker yang tidak tahu apa-apa).
"$PY" -m uvicorn agent.main:app --host "$HOST" --port "$PORT" --workers 1 \
  --log-level warning --no-access-log &
AGENT_PID=$!
WA_PID=""
CLEANED=0
cleanup() {
  [ "$CLEANED" = 1 ] && return 0
  CLEANED=1
  log "Menghentikan proses anak"
  for pid in "${WA_PID:-}" "${AGENT_PID:-}" "${PREWARM_PID:-}"; do
    [ -n "$pid" ] && kill "$pid" 2>/dev/null || true
  done
  # Stack canvas yang kita nyalakan sendiri (pid di data/canvas.pid) ikut dimatikan.
  "$PY" -m agent.canvas_engine stop >/dev/null 2>&1 || true
  # Tunggu HANYA anak yang kita kenal — `wait` polos bisa menggantung selamanya.
  for pid in "${WA_PID:-}" "${AGENT_PID:-}"; do
    [ -n "$pid" ] && wait "$pid" 2>/dev/null || true
  done
}
trap cleanup EXIT
trap 'cleanup; exit 130' INT
trap 'cleanup; exit 143' TERM
AGENT_READY=0
for attempt in $(seq 1 40); do
  if curl -fsS -H "X-Agent-Token: ${ACTION_TOKEN:-}" "http://$HOST:$PORT/health" >/dev/null 2>&1; then
    AGENT_READY=1
    break
  fi
  if ! kill -0 "$AGENT_PID" 2>/dev/null; then
    fail "Proses agent berhenti sebelum health check berhasil. Periksa konfigurasi agent dan dependensi Python."
  fi
  sleep 1
done
[ "$AGENT_READY" = 1 ] || fail "Agent tidak merespons pada http://$HOST:$PORT/health dalam 40 detik."
log "Agent siap dan health check berhasil"

# Transport WhatsApp diawasi: bila crash, dinyalakan ulang (maks 5x berturut-turut,
# jeda bertambah). Jalan stabil > 5 menit = hitungan diulang dari nol. Agent mati
# = berhenti total (state di memorinya hilang, harus start ulang bersih).
WA_RESTARTS=0
WA_STARTED=0
start_wa() {
  log "Memulai transport WhatsApp"
  node "$ROOT/transport/wa.js" &
  WA_PID=$!
  WA_STARTED=$(date +%s)
}
start_wa
while true; do
  if ! kill -0 "$AGENT_PID" 2>/dev/null; then
    fail "Proses agent berhenti."
  fi
  if ! kill -0 "$WA_PID" 2>/dev/null; then
    rc=0; wait "$WA_PID" 2>/dev/null || rc=$?
    [ $(( $(date +%s) - WA_STARTED )) -gt 300 ] && WA_RESTARTS=0
    WA_RESTARTS=$((WA_RESTARTS + 1))
    [ "$WA_RESTARTS" -le 5 ] || fail "Transport WhatsApp berhenti $WA_RESTARTS kali berturut-turut (exit $rc); berhenti mencoba."
    log "Transport WhatsApp berhenti (exit $rc); mulai ulang #$WA_RESTARTS dalam $((WA_RESTARTS * 3)) dtk"
    sleep $((WA_RESTARTS * 3))
    start_wa
  fi
  sleep 5
done
