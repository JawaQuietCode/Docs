"""terminal.py — tool `terminal`: shell dengan DIREKTORI KERJA yang bertahan.

Beda dengan `shell`
-------------------
`shell` menjalankan satu perintah di folder kerja default setiap kali. Itu cukup
untuk "jalankan ini", tetapi tidak terasa seperti terminal: `cd src` lalu
`ls` berikutnya kembali ke folder awal, sehingga tugas bertahap (masuk folder
proyek → jalankan tes → baca log) harus menulis path absolut berulang-ulang.

`terminal` menyimpan `cwd` TERAKHIR per percakapan (per chat/tenant) sehingga:

    terminal: cd skills/external && ls
    terminal: pwd            → /…/skills/external
    terminal: cat SKILL.md   → berjalan di folder yang sama

Cara kerjanya: perintah dijalankan lewat `shell` (jadi SEMUA lapisan keamanan
tetap berlaku — policy pola berbahaya, denyPaths, allowlist executable,
pembersihan environment, batas waktu, batas output), lalu `pwd` akhir dicatat
ke berkas sementara dan disimpan sebagai cwd sesi. `cwd` tetap dikurung ke area
izin oleh `shell._allowed_cwd` — model tidak bisa "keluar" ke `/etc`.

Batas yang jujur: yang bertahan hanya DIREKTORI KERJA. Variabel environment
(`export FOO=1`) dan proses latar tidak bertahan antar panggilan karena tiap
perintah tetap proses baru. Untuk pekerjaan yang butuh state, tulis skrip ke
berkas lalu jalankan (pola yang sama dengan agent coding mana pun).
"""
from __future__ import annotations

import os
import tempfile
import time

from tools.registry import register

# {kunci-sesi: {"cwd": str, "updated": ts, "runs": int}}
_SESSIONS: dict = {}
MAX_SESSIONS = 500

SCHEMA = {
    "description": (
        "Terminal dengan direktori kerja BERTAHAN antar perintah (seperti terminal "
        "sungguhan): setelah `cd folder`, perintah berikutnya berjalan di folder itu. "
        "Mendukung pipe, &&, redirect, heredoc, variabel shell, serta perintah "
        "interaktif non-TTY. Pakai `shell` untuk perintah sekali-jalan; pakai "
        "`terminal` untuk tugas bertahap (masuk folder proyek, jalankan tes, baca log). "
        "Aksi: run (default), state (lihat sesi), reset (kembali ke folder kerja awal). "
        "Catatan: hanya CWD yang bertahan; `export` dan proses latar tidak. "
        "PAKAI TOOL INI untuk MENJALANKAN sendiri — jangan menyerahkan blok perintah "
        "ke pengguna."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "command": {"type": "string",
                        "description": "Perintah shell (boleh pipe/&&/redirect/heredoc)"},
            "cwd": {"type": "string",
                    "description": "Mulai dari folder ini (relatif root proyek). Kosong = lanjutkan sesi"},
            "timeout": {"type": "integer",
                        "description": "Batas waktu detik (dibatasi security.execTimeoutSeconds)"},
            "action": {"type": "string", "enum": ["run", "state", "reset"],
                       "description": "run (default) | state | reset"},
        },
        "required": [],
    },
}


def _session_key(config) -> str:
    """Kunci sesi: satu terminal per percakapan (fallback ke tenant/global)."""
    ctx = (config or {}).get("context") or {}
    for key in ("chat_id", "tenant"):
        val = str(ctx.get(key) or "").strip()
        if val:
            return val
    return "global"


def _default_cwd(config) -> str:
    """Folder kerja awal — sama dengan yang dipakai tool `shell`."""
    root = str((config or {}).get("root") or ".")
    sec = (config or {}).get("security") or {}
    workdir = sec.get("workspaceDir", "./workspace")
    try:
        from security.tenant_context import current_workspace
        tw = current_workspace()
        if tw:
            workdir = tw
    except Exception:
        pass
    path = str(workdir)
    if not os.path.isabs(path):
        path = os.path.join(root, path)
    return os.path.normpath(path)


def _confine(path: str, config):
    """Kurung path ke area izin lewat aturan `shell` (sumber tunggal)."""
    try:
        from tools.builtin.shell import _allowed_cwd
        return _allowed_cwd(path, config)
    except Exception:
        return os.path.normpath(path), ""


def _prune() -> None:
    if len(_SESSIONS) <= MAX_SESSIONS:
        return
    for key, _ in sorted(_SESSIONS.items(), key=lambda kv: kv[1].get("updated", 0))[:50]:
        _SESSIONS.pop(key, None)


def handler(args, config=None):
    args = args or {}
    action = str(args.get("action") or "run").strip().lower()
    key = _session_key(config)
    sess = _SESSIONS.get(key) or {}

    if action in ("state", "status", "info"):
        return {
            "ok": True,
            "sesi": key,
            "cwd": sess.get("cwd") or _default_cwd(config),
            "cwd_awal": _default_cwd(config),
            "perintah_dijalankan": int(sess.get("runs") or 0),
            "catatan": "Hanya cwd yang bertahan; `export` dan proses latar tidak.",
        }

    if action in ("reset", "clear", "cd-home"):
        _SESSIONS.pop(key, None)
        return {"ok": True, "sesi": key, "cwd": _default_cwd(config),
                "note": "sesi terminal direset ke folder kerja awal"}

    cmd = str(args.get("command") or "").strip()
    if not cmd:
        return {"error": "command kosong"}

    # CWD awal: argumen eksplisit → sesi sebelumnya → folder kerja default.
    cwd = str(args.get("cwd") or sess.get("cwd") or _default_cwd(config))
    cwd, cwd_note = _confine(cwd, config)

    # Perintah dibungkus: jalankan apa adanya, lalu tulis `pwd` akhir ke berkas
    # sementara. `exit $__rc` menjaga kode keluar perintah tetap utuh.
    fd, cwd_file = tempfile.mkstemp(prefix="kwee-cwd-", suffix=".txt")
    os.close(fd)
    wrapped = f"{cmd}\n__kwee_rc=$?\npwd > {cwd_file} 2>/dev/null\nexit $__kwee_rc"

    from tools.builtin import shell as shell_tool
    res = shell_tool.handler(
        {"command": wrapped, "cwd": cwd,
         "timeout": args.get("timeout")},
        config,
    )

    # Baca cwd hasil (kalau perintah memanggil `exit`, berkas kosong → sesi lama).
    new_cwd = ""
    try:
        with open(cwd_file, "r", encoding="utf-8", errors="replace") as fh:
            new_cwd = (fh.read() or "").strip().splitlines()[-1].strip() if fh else ""
    except Exception:
        new_cwd = ""
    finally:
        try:
            os.unlink(cwd_file)
        except Exception:
            pass

    if new_cwd:
        confined, note = _confine(new_cwd, config)
        if os.path.isdir(confined):
            if os.path.normpath(confined) != os.path.normpath(new_cwd):
                # Perintah keluar dari area izin (mis. `cd /etc`): JANGAN simpan
                # sebagai cwd sesi — sesi tetap di folder sebelumnya, dan model
                # diberi tahu supaya tidak mengira sudah pindah.
                cwd_note = (f"cwd '{new_cwd}' di luar area izin; sesi tetap di "
                            f"{sess.get('cwd') or cwd}")
                new_cwd = ""
            else:
                new_cwd = confined
                if note:
                    cwd_note = note
    if new_cwd:
        _prune()
        _SESSIONS[key] = {"cwd": new_cwd, "updated": time.time(),
                          "runs": int(sess.get("runs") or 0) + 1}

    out = dict(res or {})
    out["cwd"] = new_cwd or sess.get("cwd") or cwd
    out["sesi"] = key
    if cwd_note and "note" not in out:
        out["note"] = cwd_note
    return out


register("terminal", SCHEMA, handler, toolset="core", dangerous=True, concurrent=False)
