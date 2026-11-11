#!/usr/bin/env bash
# Idempotent, project-local setup for Rust Token Killer. Source from setup.sh.
kwee_setup_rtk() {
  local rtk_on="${KWEE_RTK:-1}"
  local rtk_ready=0 rtk_probe rtk_rc install_log install_script
  case "$rtk_on" in 0|false|off|no) rtk_on=0 ;; *) rtk_on=1 ;; esac
  [ "$rtk_on" = "1" ] || return 0

  export PATH="$HOME/.local/bin:$PATH"
  hash -r 2>/dev/null || true
  export RTK_TELEMETRY_DISABLED=1
  mkdir -p "$HOME/.local/bin"

  # `rtk` is an ambiguous executable name: verify Rust Token Killer via gain.
  if command -v rtk >/dev/null 2>&1 && rtk gain >/dev/null 2>&1; then
    rtk_ready=1
  fi

  if [ "$rtk_ready" != "1" ] && command -v curl >/dev/null 2>&1; then
    install_script="$TMPDIR/rtk-install.sh"
    install_log="$TMPDIR/rtk-install.log"
    printf '[setup] %s\n' 'memasang RTK upstream ke ~/.local/bin (checksum upstream diverifikasi)...'
    if curl -fsSL https://raw.githubusercontent.com/rtk-ai/rtk/refs/heads/master/install.sh \
        -o "$install_script" &&
       RTK_EDITION=oss RTK_INSTALL_DIR="$HOME/.local/bin" sh "$install_script" >"$install_log" 2>&1; then
      :
    else
      tail -20 "$install_log" >&2 2>/dev/null || true
      printf '[setup] %s\n' 'PERINGATAN: instalasi RTK gagal; Kwee tetap berjalan dengan shell asli'
    fi
  fi

  hash -r 2>/dev/null || true
  if command -v rtk >/dev/null 2>&1 && rtk gain >/dev/null 2>&1; then
    rtk_ready=1
  else
    rtk_ready=0
  fi

  if [ "$rtk_ready" = "1" ]; then
    printf '[setup] RTK Rust Token Killer terverifikasi: %s\n' "$(rtk --version 2>/dev/null | head -1 || true)"
    # A rewrite candidate is indicated by exit 0. Exit 1 means no supported
    # equivalent. Both must be caught explicitly so `set -e` won't abort setup.
    if rtk_probe="$(rtk rewrite 'git status' 2>/dev/null)"; then
      if [ -n "$rtk_probe" ]; then
        printf '[setup] %s\n' 'RTK command rewrite siap untuk shell/terminal Kwee'
      else
        printf '[setup] %s\n' 'PERINGATAN: RTK rewrite tidak mengeluarkan command; fallback asli aktif'
      fi
    else
      rtk_rc=$?
      printf '[setup] RTK rewrite self-test exit=%s; fallback asli tetap aktif\n' "$rtk_rc"
    fi
  else
    printf '[setup] %s\n' 'PERINGATAN: RTK tidak tersedia/identitas tidak valid; Kwee memakai shell asli'
  fi
}
