"""env_sandbox.py — bangun environment BERSIH untuk proses anak.

Kenapa modul ini ada: `agent/config.py` memuat seluruh isi `.env` ke
`os.environ` (LLM_API_KEY, ACTION_TOKEN, WA_*, PTERO_*). Versi lama `shell.py`
dan `agent/mechanisms.py` meneruskan `dict(os.environ)` apa adanya ke proses
anak — sehingga `env` / `printenv` (keduanya ada di allowlist) mencetak kunci
rahasia ke percakapan WhatsApp, dan CLI eksternal (claude/codex/hermes) ikut
mewarisi kunci itu.

Aturan di sini: **allowlist positif** (hanya variabel yang memang dibutuhkan
proses anak) + saringan prefix/suffix rahasia sebagai sabuk kedua.
"""
import os

# Variabel yang boleh diwariskan (bukan rahasia, dibutuhkan tool).
KEEP_ENV = {
    "PATH", "HOME", "LANG", "TMPDIR", "TZ", "TERM", "SHELL", "USER", "LOGNAME",
    "PWD", "PYTHONPATH", "PYTHONUSERBASE", "PYTHONUNBUFFERED", "PYTHONDONTWRITEBYTECODE",
    "NODE_PATH", "NODE_OPTIONS", "NPM_CONFIG_PREFIX", "NPM_CONFIG_CACHE",
    "VIRTUAL_ENV", "CONDA_PREFIX", "EDITOR", "GIT_TERMINAL_PROMPT", "GIT_SSH_COMMAND",
    "SSL_CERT_FILE", "SSL_CERT_DIR", "REQUESTS_CA_BUNDLE", "CURL_CA_BUNDLE",
    "PLAYWRIGHT_BROWSERS_PATH", "DISPLAY",
    # Bukan rahasia, dan sangat membantu agent: root proyek + interpreter yang
    # punya dependensi. Tanpa KWEE_ROOT, aturan "coba $KWEE_ROOT/agent" di
    # prompts/rules.md tidak bisa dijalankan oleh tool shell.
    "KWEE_ROOT", "KWEE_PY",
}
# Prefix variabel rahasia milik proyek ini.
DENY_PREFIX = ("LLM_", "WA_", "ACTION_", "PTERO_", "OWNER_", "AGENT_TOKEN", "KWEE_SECRET")
# Suffix yang menandakan rahasia, apa pun nama depannya.
DENY_SUFFIX = ("_KEY", "_TOKEN", "_SECRET", "_PASSWORD", "_PASS", "_APIKEY", "_CREDENTIALS")


def _is_secret(key: str) -> bool:
    k = (key or "").upper()
    if k.startswith(DENY_PREFIX):
        return True
    return any(k.endswith(s) for s in DENY_SUFFIX)


def child_env(root: str, extra: dict | None = None) -> dict:
    """Environment untuk proses anak: aman, dengan PATH yang sudah dilengkapi.

    `root` dipakai sebagai HOME cadangan (pola Pterodactyl: HOME = folder proyek)
    dan untuk menemukan `~/.local/bin` (yt-dlp/ffmpeg) serta
    `$PYTHONUSERBASE/bin` (skrip pip --user seperti graphify).
    """
    env = {}
    for k, v in os.environ.items():
        if _is_secret(k):
            continue
        if k in KEEP_ENV or k.startswith(("LC_", "XDG_")):
            env[k] = v
    env.setdefault("HOME", root or os.path.expanduser("~"))
    env.setdefault("LANG", "C.UTF-8")
    env["PYTHONUNBUFFERED"] = "1"
    env["GIT_PAGER"] = "cat"
    env["PAGER"] = "cat"
    env["NO_COLOR"] = "1"
    env["TERM"] = "dumb"

    home = env.get("HOME") or root
    local_bin = os.path.join(home, ".local", "bin")
    env["PATH"] = local_bin + os.pathsep + env.get("PATH", "/usr/local/bin:/usr/bin:/bin")
    pub = env.get("PYTHONUSERBASE")
    if pub:
        env["PATH"] = os.path.join(pub, "bin") + os.pathsep + env["PATH"]

    for k, v in (extra or {}).items():
        if v is not None:
            env[str(k)] = str(v)
    return env


def redacted_environ() -> dict:
    """Salinan os.environ dengan nilai rahasia disamarkan (untuk log/audit)."""
    out = {}
    for k, v in os.environ.items():
        if _is_secret(k):
            out[k] = "***"
        else:
            out[k] = v
    return out
