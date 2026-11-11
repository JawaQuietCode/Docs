#!/usr/bin/env bash
set -Eeuo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
HELPER="$ROOT/scripts/setup_rtk.sh"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

fail() { printf 'FAIL: %s\n' "$*" >&2; exit 1; }
assert_contains() { grep -Fq -- "$2" "$1" || fail "expected '$2' in $1"; }

# Scenario 1: valid upstream RTK is detected, rewrite is smoke-tested, and
# installer/curl are not needed.
mkdir -p "$TMP/valid-bin" "$TMP/home-valid/.local/bin" "$TMP/tmp-valid"
cat > "$TMP/valid-bin/rtk" <<'RTK'
#!/usr/bin/env bash
case "${1:-}" in
  gain) exit 0 ;;
  --version) echo 'rtk 9.9.9' ;;
  rewrite) echo 'rtk git status' ;;
  *) exit 1 ;;
esac
RTK
chmod +x "$TMP/valid-bin/rtk"
(
  HOME="$TMP/home-valid" TMPDIR="$TMP/tmp-valid" PATH="$TMP/valid-bin:/usr/bin:/bin" KWEE_RTK=1
  export HOME TMPDIR PATH KWEE_RTK
  source "$HELPER"
  kwee_setup_rtk >"$TMP/valid.out" 2>&1
)
assert_contains "$TMP/valid.out" 'RTK Rust Token Killer terverifikasi'
assert_contains "$TMP/valid.out" 'rewrite siap'

# Scenario 2: a different program named rtk fails `gain`; the verified local
# installer path replaces it and the installed binary is probed again.
mkdir -p "$TMP/wrong-bin" "$TMP/home-install/.local/bin" "$TMP/tmp-install" "$TMP/stub-bin"
cat > "$TMP/wrong-bin/rtk" <<'WRONG'
#!/usr/bin/env bash
[ "${1:-}" = "--version" ] && echo 'other rtk 1.0' || exit 42
WRONG
chmod +x "$TMP/wrong-bin/rtk"
cat > "$TMP/stub-bin/curl" <<'CURL'
#!/usr/bin/env bash
out=''
while [ "$#" -gt 0 ]; do
  if [ "$1" = '-o' ]; then out="$2"; shift 2; else shift; fi
done
[ -n "$out" ] || exit 2
cat > "$out" <<'INSTALLER'
#!/usr/bin/env sh
[ "${RTK_EDITION:-}" = "oss" ] || exit 9
mkdir -p "$RTK_INSTALL_DIR"
cat > "$RTK_INSTALL_DIR/rtk" <<'RTK'
#!/usr/bin/env sh
case "${1:-}" in
  gain) exit 0 ;;
  --version) echo 'rtk test-installer' ;;
  rewrite) echo 'rtk git status' ;;
  *) exit 1 ;;
esac
RTK
chmod +x "$RTK_INSTALL_DIR/rtk"
INSTALLER
CURL
chmod +x "$TMP/stub-bin/curl"
(
  HOME="$TMP/home-install" TMPDIR="$TMP/tmp-install" PATH="$TMP/home-install/.local/bin:$TMP/wrong-bin:$TMP/stub-bin:/usr/bin:/bin" KWEE_RTK=1
  export HOME TMPDIR PATH KWEE_RTK
  source "$HELPER"
  kwee_setup_rtk >"$TMP/install.out" 2>&1
)
assert_contains "$TMP/install.out" 'memasang RTK upstream'
assert_contains "$TMP/install.out" 'rewrite siap'
[ -x "$TMP/home-install/.local/bin/rtk" ] || fail 'installer did not place rtk in project-local bin'

# Scenario 3: explicit opt-out is side-effect-free and does not install.
mkdir -p "$TMP/home-disabled" "$TMP/tmp-disabled" "$TMP/disabled-bin"
(
  HOME="$TMP/home-disabled" TMPDIR="$TMP/tmp-disabled" PATH="$TMP/disabled-bin:/usr/bin:/bin" KWEE_RTK=0
  export HOME TMPDIR PATH KWEE_RTK
  source "$HELPER"
  kwee_setup_rtk >"$TMP/disabled.out" 2>&1
)
[ ! -d "$TMP/home-disabled/.local/bin" ] || fail 'disabled mode unexpectedly created install directory'
[ ! -s "$TMP/disabled.out" ] || fail 'disabled mode unexpectedly logged or installed'

printf 'RTK setup smoke tests passed (valid binary, name collision/install fallback, opt-out).\n'
