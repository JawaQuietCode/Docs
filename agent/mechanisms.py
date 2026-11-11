"""mechanisms.py — registri MEKANISME (engine) per owner.

Tiap owner boleh memakai mekanisme BERBEDA, dan tiap mekanisme punya RUANGAN
memori + SESI sendiri (lihat ``memory.tenants.room_paths``):

  - ``native`` — bot bawaan Kwee (loop agent sendiri). SELALU tersedia.
  - ``hermes`` — delegasi ke Hermes (`hermes`) bila terpasang.
  - ``claude`` — delegasi ke Claude Code (`claude`) bila terpasang.
  - ``codex``  — delegasi ke Codex CLI (`codex`) bila terpasang.
  - ``canvas`` — delegasi ke OpenHands lewat stack `@openhands/agent-canvas`
                 (HTTP ke agent-server lokal; lihat ``agent/canvas_engine.py``).

Mekanisme eksternal hanya dipakai bila perintahnya ADA di PATH; kalau tidak,
otomatis jatuh ke ``native`` supaya bot tidak pernah mati karena mekanisme yang
belum dipasang.

SESI per ruangan: tiap engine menyimpan state-nya di folder sendiri. Agar tidak
bercampur antar-owner, ``run_external`` mengarahkan folder config/sesi tiap
engine ke dalam ruangan owner:

  codex  → CODEX_HOME          = <room>/sessions/codex
  claude → CLAUDE_CONFIG_DIR   = <room>/sessions/claude

(Hermes TIDAK diganti ``HERMES_HOME``-nya karena variabel itu sekaligus memuat
folder KODE install Hermes — menggantinya akan merusak CLI-nya.)

Mekanisme HANYA menggantikan cara MEMBUAT jawaban; format balasan (kartu
GenAI*), tool WhatsApp, dan ruangan memori tetap dikelola lapisan yang sama.

Perbaikan dibanding versi lama:
  * prompt PANJANG dikirim lewat stdin (argv punya batas ±128KB → E2BIG).
  * proses anak dijalankan di session sendiri dan dibunuh SATU GROUP saat
    timeout (dulu hanya anak langsung yang mati, cucunya jadi yatim).
  * environment TIDAK lagi mewarisi kunci rahasia (`security/env_sandbox.py`).
  * `smoke_test()` memeriksa CLI sekali saat startup (versi + flag), supaya
    perubahan flag tidak baru ketahuan saat owner memakainya.
"""
from __future__ import annotations

import os
import shutil
import signal
import subprocess

from memory import tenants
from security.env_sandbox import child_env

# Perintah CLI tiap mekanisme (None = mekanisme bawaan, tanpa CLI).
_MECH_CLI = {
    "native": None,
    "hermes": ("hermes",),
    "claude": ("claude",),
    "codex": ("codex",),
    # canvas tidak punya mode sekali-jalan: ia server. Argv-nya hanya dipakai
    # untuk memeriksa keberadaan executable; panggilan sebenarnya lewat HTTP.
    "canvas": ("agent-canvas",),
}

# Flag non-interaktif agar CLI mengembalikan jawaban sekali jalan (print mode).
_MECH_ARGS = {
    "hermes": ["-p", "{prompt}"],
    "claude": ["-p", "{prompt}"],
    "codex": ["exec", "{prompt}"],
}

# Argv yang dipakai saat prompt dikirim lewat STDIN (placeholder dibuang).
_MECH_STDIN_ARGS = {
    "hermes": ["-p", "-"],
    "claude": ["-p", "-"],
    "codex": ["exec", "-"],
}

# Ambang prompt yang dikirim lewat stdin (argv Linux dibatasi ±128KB total).
_STDIN_THRESHOLD = 60000

# Variabel env yang menunjuk folder sesi/config engine → subfolder ruangan.
# HANYA engine dengan env var khusus-config (aman diganti) — bukan yang memuat
# KODE install sekaligus. Hermes memakai HERMES_HOME untuk install + runtime,
# jadi TIDAK diganti (akan merusak CLI-nya).
_MECH_SESSION_ENV = {
    "claude": "CLAUDE_CONFIG_DIR",
    "codex": "CODEX_HOME",
}

# Hasil smoke test per mekanisme (diisi `smoke_test()`).
_HEALTH: dict = {}


def _canvas_bin() -> str:
    """Executable agent-canvas (PATH global, npx, atau node_modules lokal)."""
    p = shutil.which("agent-canvas")
    if p:
        return p
    lokal = os.path.join(os.getcwd(), "node_modules", ".bin", "agent-canvas")
    return lokal if os.path.exists(lokal) else ""


def available() -> dict:
    """Peta mekanisme → tersedia? (perintahnya ada di PATH)."""
    out = {}
    for name, cli in _MECH_CLI.items():
        if cli is None:
            out[name] = True
        elif name == "canvas":
            out[name] = bool(_canvas_bin())
        else:
            out[name] = bool(shutil.which(cli[0]))
    return out


def resolve(name, config: dict | None = None) -> str:
    """Nama mekanisme EFEKTIF: diminta, tetapi jatuh ke native bila tak tersedia."""
    want = tenants.normalize_mechanism(name)
    if want == "native":
        return "native"
    if want == "canvas":
        return "canvas" if _canvas_bin() else "native"
    cli = _MECH_CLI.get(want)
    if cli and shutil.which(cli[0]):
        return want
    return "native"


def command_for(mechanism: str, prompt: str) -> list | None:
    """Argv perintah CLI untuk mekanisme ini, atau None bila native."""
    m = tenants.normalize_mechanism(mechanism)
    cli = _MECH_CLI.get(m)
    if not cli:
        return None
    if len(prompt or "") > _STDIN_THRESHOLD and m in _MECH_STDIN_ARGS:
        return list(cli) + list(_MECH_STDIN_ARGS[m])
    argv = list(cli)
    for a in _MECH_ARGS.get(m, []):
        argv.append(prompt if a == "{prompt}" else a)
    return argv


def session_env(mechanism: str, sessions_dir=None) -> dict:
    """Env yang mengarahkan folder sesi engine ke ruangan owner.

    ``sessions_dir`` = folder ``sessions`` milik ruangan (dari room_paths).
    Mengembalikan {} bila tidak ada/mekanisme native.
    """
    m = tenants.normalize_mechanism(mechanism)
    var = _MECH_SESSION_ENV.get(m)
    if not var or not sessions_dir:
        return {}
    target = os.path.join(str(sessions_dir), m)
    try:
        os.makedirs(target, exist_ok=True)
    except Exception:
        pass
    return {var: target}


def _kill_group(proc: subprocess.Popen) -> None:
    """Bunuh seluruh process group anak (bukan hanya CLI-nya)."""
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(os.getpgid(proc.pid), sig)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass
        try:
            proc.wait(timeout=2)
            return
        except Exception:
            continue


def run_external(mechanism: str, prompt: str, *, cwd=None, timeout=600,
                 sessions_dir=None, env_extra=None, config=None) -> dict:
    """Jalankan mekanisme eksternal sekali-jalan. Kembalikan {ok, text|error}."""
    m = tenants.normalize_mechanism(mechanism)
    config_ref = config
    if m == "canvas":
        # Bukan proses sekali-jalan: kirim tugas ke stack agent-canvas.
        try:
            from agent import canvas_engine
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": f"modul canvas tidak bisa dimuat: {exc}"}
        cfg = dict(config_ref or {})
        if sessions_dir:
            cfg.setdefault("mechanisms", {}).setdefault("canvas", {})
        hasil = canvas_engine.ask(prompt, cfg, timeout=timeout, cwd=cwd)
        if hasil.get("ok"):
            return {"ok": True, "text": hasil.get("text") or "",
                    "conversation_id": hasil.get("conversation_id"), "exit_code": 0}
        return {"ok": False, "error": hasil.get("error") or "canvas gagal"}
    argv = command_for(m, prompt)
    if not argv:
        return {"ok": False, "error": "bukan mekanisme eksternal"}
    use_stdin = len(prompt or "") > _STDIN_THRESHOLD and m in _MECH_STDIN_ARGS
    # Environment bersih: kunci LLM/WA/PTERO tidak ikut ke CLI eksternal.
    env = child_env(os.environ.get("HOME", os.getcwd()),
                    extra=session_env(m, sessions_dir))
    if env_extra:
        env.update({k: str(v) for k, v in env_extra.items() if v is not None})
    try:
        proc = subprocess.Popen(
            argv,
            stdin=subprocess.PIPE if use_stdin else subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, cwd=cwd or None, env=env, start_new_session=True,
        )
    except FileNotFoundError:
        return {"ok": False, "error": f"perintah '{argv[0]}' tidak ditemukan"}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"gagal menjalankan {m}: {exc}"}
    try:
        out, err = proc.communicate(prompt if use_stdin else None, timeout=timeout)
    except subprocess.TimeoutExpired:
        _kill_group(proc)
        return {"ok": False, "error": f"mekanisme {m} melebihi batas {timeout} detik"}
    except Exception as exc:  # noqa: BLE001
        _kill_group(proc)
        return {"ok": False, "error": f"gagal menjalankan {m}: {exc}"}
    text = (out or "").strip()
    if not text and proc.returncode != 0:
        return {"ok": False, "error": (err or f"exit {proc.returncode}").strip()[:500]}
    return {"ok": True, "text": text, "exit_code": proc.returncode,
            "stderr": (err or "").strip()[:500]}


def smoke_test(timeout: int = 20) -> dict:
    """Periksa tiap CLI sekali (versi + bisa dijalankan). Simpan di `_HEALTH`.

    `available()` hanya melihat `which`. Kalau flag `-p`/`exec` berubah di versi
    baru, dulu bot baru tahu ketika owner memakainya (dan gagal di tengah turn).
    """
    _HEALTH.clear()
    for name, cli in _MECH_CLI.items():
        if cli is None:
            _HEALTH[name] = {"available": True, "version": "native"}
            continue
        path = shutil.which(cli[0])
        if not path:
            _HEALTH[name] = {"available": False, "error": "tidak ada di PATH"}
            continue
        try:
            proc = subprocess.run([path, "--version"], capture_output=True, text=True,
                                  timeout=timeout, env=child_env(os.getcwd()),
                                  start_new_session=True)
            _HEALTH[name] = {"available": True,
                             "version": (proc.stdout or proc.stderr or "").strip()[:120],
                             "exit_code": proc.returncode}
        except Exception as exc:  # noqa: BLE001
            _HEALTH[name] = {"available": True, "version": "", "error": str(exc)[:200]}
    return dict(_HEALTH)


def health() -> dict:
    """Hasil smoke test terakhir (kosong bila belum dijalankan)."""
    return dict(_HEALTH)
