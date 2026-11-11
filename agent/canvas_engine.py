"""canvas_engine.py — engine OpenHands lewat `@openhands/agent-canvas`.

Kenapa modul ini ada
--------------------
`agent-canvas` bukan CLI sekali-jalan seperti `codex exec` atau `hermes -p`.
Ia menjalankan **stack**: agent-server (uvx, port 18000), automation (18001),
dan frontend statis di balik ingress (port 8000). Karena itu integrasinya
berupa KLIEN HTTP, bukan argv:

  · `ensure_stack()`  — nyalakan stack bila belum hidup (sekali, lalu dipakai
                        ulang). Log ke data/canvas.log, pid ke data/canvas.pid.
  · `ask()`           — buat percakapan, tunggu selesai, ambil jawaban akhir.
  · `status()`        — apakah stack hidup, port, versi.
  · `stop()`          — hentikan stack yang kita nyalakan sendiri.

Semua panggilan memakai `X-API-Key` + `Authorization: Bearer` sekaligus, karena
versi agent-server berbeda-beda soal header mana yang diperiksa. Bentuk respons
juga dibaca toleran (beberapa kemungkinan nama field), supaya perubahan kecil di
upstream tidak langsung mematikan bot.

CLI:
    python3 -m agent.canvas_engine status
    python3 -m agent.canvas_engine start
    python3 -m agent.canvas_engine stop
    python3 -m agent.canvas_engine ask "perbaiki test yang gagal"
"""
from __future__ import annotations

import json
import os
import secrets
import shutil
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

DEFAULTS = {
    "port": 8000,
    "autoStart": True,
    "startTimeoutSeconds": 600,
    "timeoutSeconds": 1800,
    "pollSeconds": 4,
}


# --------------------------------------------------------------------------
# setelan
# --------------------------------------------------------------------------
def _cfg(config: dict | None) -> dict:
    raw = ((config or {}).get("mechanisms") or {}).get("canvas") or {}
    out = dict(DEFAULTS)
    for k, v in raw.items():
        if v is not None:
            out[k] = v
    return out


def _root(config: dict | None) -> Path:
    return Path((config or {}).get("root") or os.getcwd()).resolve()


def _data_dir(config: dict | None) -> Path:
    d = _root(config) / "data"
    d.mkdir(parents=True, exist_ok=True)
    return d


def api_key(config: dict | None) -> str:
    """Kunci API stack. Dibuat sekali dan disimpan, supaya restart tetap sama."""
    cfg = _cfg(config)
    env_key = (os.environ.get("LOCAL_BACKEND_API_KEY") or os.environ.get("KWEE_CANVAS_KEY") or "").strip()
    if env_key:
        return env_key
    if cfg.get("apiKey"):
        return str(cfg["apiKey"])
    f = _data_dir(config) / "canvas.key"
    try:
        if f.exists():
            k = f.read_text(encoding="utf-8").strip()
            if k:
                return k
        k = "kwee-" + secrets.token_hex(16)
        f.write_text(k + "\n", encoding="utf-8")
        try:
            f.chmod(0o600)
        except Exception:
            pass
        return k
    except Exception:
        return "kwee-local"


def base_url(config: dict | None) -> str:
    port = int(_cfg(config).get("port") or 8000)
    return f"http://127.0.0.1:{port}"


# --------------------------------------------------------------------------
# HTTP
# --------------------------------------------------------------------------
def _headers(config: dict | None) -> dict:
    k = api_key(config)
    return {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "X-API-Key": k,
        "Authorization": "Bearer " + k,
    }


def _req(config: dict | None, method: str, path: str, body=None, timeout: int = 60):
    """Kembalikan (data, error). Tidak pernah melempar."""
    url = base_url(config) + path
    data = None
    if body is not None:
        data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=_headers(config), method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", "replace") or "{}"
            try:
                return json.loads(raw), None
            except Exception:
                return {"raw": raw}, None
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            detail = exc.read().decode("utf-8", "replace")[:400]
        except Exception:
            pass
        return None, {"status": exc.code, "detail": detail}
    except Exception as exc:  # noqa: BLE001
        return None, {"status": 0, "detail": str(exc)}


# --------------------------------------------------------------------------
# stack
# --------------------------------------------------------------------------
def _pid_file(config: dict | None) -> Path:
    return _data_dir(config) / "canvas.pid"


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except Exception:
        return False


def _baca_pid(config: dict | None) -> int:
    f = _pid_file(config)
    try:
        pid = int(f.read_text(encoding="utf-8").strip() or 0)
    except Exception:
        return 0
    return pid if pid and _alive(pid) else 0


def _health(config: dict | None, timeout: int = 5) -> dict:
    """Bedakan service hidup dari API yang benar-benar terautentikasi."""
    data, err = _req(config, "GET", "/api/conversations", timeout=timeout)
    if data is not None:
        return {"reachable": True, "authorized": True, "status": 200}
    status = int((err or {}).get("status") or 0)
    return {"reachable": status in (401, 403), "authorized": False if status in (401, 403) else None,
            "status": status, "detail": (err or {}).get("detail", "")}


def ping(config: dict | None, timeout: int = 5) -> bool:
    """True bila ingress hidup; status auth diperiksa terpisah oleh ensure_stack."""
    return bool(_health(config, timeout).get("reachable"))


def binary() -> str:
    """Path executable `agent-canvas` (global npm, npx, atau node_modules lokal)."""
    for kandidat in ("agent-canvas",):
        p = shutil.which(kandidat)
        if p:
            return p
    lokal = Path.cwd() / "node_modules" / ".bin" / "agent-canvas"
    if lokal.exists():
        return str(lokal)
    return ""


def status(config: dict | None) -> dict:
    cfg = _cfg(config)
    health = _health(config)
    return {
        "running": bool(health.get("reachable")),
        "authorized": health.get("authorized"),
        "http_status": health.get("status"),
        "auth_detail": health.get("detail", ""),
        "port": cfg["port"],
        "url": base_url(config),
        "binary": binary(),
        "pid": _baca_pid(config),
        "log": str(_data_dir(config) / "canvas.log"),
    }


def ensure_stack(config: dict | None, wait: bool = True) -> dict:
    """Pastikan stack hidup. Kembalikan {ok, started, error}."""
    health = _health(config)
    if health.get("authorized") is True:
        return {"ok": True, "started": False}
    if health.get("reachable") and health.get("authorized") is False:
        # 401/403 bukan health yang sehat. Jika PID milik kita tercatat, restart
        # stack dengan key terbaru; jangan membunuh service eksternal yang tidak kita miliki.
        if _baca_pid(config):
            print("[canvas] API 401/403; restart stack milik Kwee dengan kredensial aktif", flush=True)
            stop(config)
        else:
            return {"ok": False, "started": False,
                    "error": (f"Canvas merespons HTTP {health.get('status')} tetapi menolak autentikasi. "
                               "Stack bukan proses yang dikelola Kwee; periksa LOCAL_BACKEND_API_KEY / KWEE_CANVAS_KEY.")}
    cfg = _cfg(config)
    exe = binary()
    if not exe:
        return {"ok": False, "started": False,
                "error": ("`agent-canvas` belum terpasang. Jalankan: "
                          "npm install -g @openhands/agent-canvas")}
    if not cfg.get("autoStart", True):
        return {"ok": False, "started": False,
                "error": f"stack belum hidup di {base_url(config)} dan autoStart dimatikan"}

    env = dict(os.environ)
    env["LOCAL_BACKEND_API_KEY"] = api_key(config)
    env["OH_BIND_HOST"] = "127.0.0.1"
    # Kredensial LLM: agent-server OpenHands punya nama sendiri. Tanpa ini,
    # stack menyala tapi tidak bisa berpikir ("model not configured").
    # Nilai diambil dari config Kwee, bukan diketik ulang.
    _llm = (config or {}).get("llm") or {}
    _pasangan = {
        "LLM_MODEL": _llm.get("model"),
        "LLM_API_KEY": _llm.get("apiKey"),
        "LLM_BASE_URL": _llm.get("baseUrl") or _llm.get("baseURL"),
    }
    for _k, _v in _pasangan.items():
        if _v and not env.get(_k):
            env[_k] = str(_v)
    env.setdefault("LLM_TEMPERATURE", str(_llm.get("temperature", 0.2)))
    env.setdefault("NODE_NO_WARNINGS", "1")
    log = open(_data_dir(config) / "canvas.log", "ab")
    try:
        proc = subprocess.Popen(
            [exe, "--backend-only", "-p", str(cfg["port"])],
            stdout=log, stderr=log, stdin=subprocess.DEVNULL,
            env=env, start_new_session=True,
        )
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "started": False, "error": f"gagal menyalakan agent-canvas: {exc}"}
    try:
        _pid_file(config).write_text(str(proc.pid), encoding="utf-8")
    except Exception:
        pass
    if not wait:
        return {"ok": True, "started": True, "pid": proc.pid}

    batas = time.time() + float(cfg.get("startTimeoutSeconds") or 600)
    while time.time() < batas:
        _health_now = _health(config)
        if _health_now.get("authorized") is True:
            return {"ok": True, "started": True, "pid": proc.pid}
        if _health_now.get("reachable") and _health_now.get("authorized") is False:
            return {"ok": False, "started": True,
                    "error": (f"Canvas hidup tetapi autentikasi gagal (HTTP {_health_now.get('status')}). "
                               "Pastikan LOCAL_BACKEND_API_KEY sama dengan key stack.")}
        if proc.poll() is not None:
            return {"ok": False, "started": True,
                    "error": f"agent-canvas berhenti (exit {proc.returncode}); lihat data/canvas.log"}
        time.sleep(3)
    return {"ok": False, "started": True,
            "error": (f"stack belum siap setelah {cfg.get('startTimeoutSeconds')} detik "
                      "(uvx mungkin sedang mengunduh agent-server). Lihat data/canvas.log")}


def stop(config: dict | None) -> dict:
    pid = _baca_pid(config)
    if not pid:
        return {"ok": True, "stopped": False, "note": "tidak ada stack yang kami nyalakan"}
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(os.getpgid(pid), sig)
        except Exception:
            try:
                os.kill(pid, sig)
            except Exception:
                pass
        time.sleep(1)
        if not _alive(pid):
            break
    try:
        _pid_file(config).unlink()
    except Exception:
        pass
    return {"ok": True, "stopped": True, "pid": pid}


# --------------------------------------------------------------------------
# percakapan
# --------------------------------------------------------------------------
_STATUS_SELESAI = {"done", "finished", "completed", "complete", "stopped",
                   "idle", "error", "failed", "cancelled", "canceled"}
_STATUS_GAGAL = {"error", "failed", "cancelled", "canceled"}


def _id_dari(j: dict) -> str:
    for k in ("id", "conversation_id", "conversationId", "session_id"):
        v = j.get(k) if isinstance(j, dict) else None
        if v:
            return str(v)
    for k in ("conversation", "session", "data"):
        v = j.get(k) if isinstance(j, dict) else None
        if isinstance(v, dict):
            for kk in ("id", "conversation_id"):
                if v.get(kk):
                    return str(v[kk])
    return ""


def _status_dari(j: dict) -> str:
    if not isinstance(j, dict):
        return ""
    for k in ("status", "execution_status", "state", "conversation_status"):
        v = j.get(k)
        if isinstance(v, str) and v:
            return v.lower()
    for k in ("conversation", "data"):
        if isinstance(j.get(k), dict):
            return _status_dari(j[k])
    return ""


def _teks_dari(j) -> str:
    """Ambil teks jawaban dari berbagai bentuk respons."""
    if isinstance(j, str):
        return j.strip()
    if not isinstance(j, dict):
        return ""
    for k in ("response", "final_response", "content", "message", "text", "answer", "output"):
        v = j.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()
        if isinstance(v, dict):
            t = _teks_dari(v)
            if t:
                return t
    for k in ("data", "conversation", "result", "events"):
        v = j.get(k)
        if isinstance(v, (dict, list)):
            t = _teks_dari(v)
            if t:
                return t
    return ""


def _buat(config: dict | None, prompt: str) -> tuple:
    """POST /api/conversations dengan beberapa bentuk body (versi berbeda)."""
    bodies = [
        {"initial_user_msg": prompt},
        {"initial_user_msg": prompt, "workspace_mode": "local_repo"},
        {"initialUserMsg": prompt},
        {"task": prompt},
    ]
    last = None
    for b in bodies:
        data, err = _req(config, "POST", "/api/conversations", b, timeout=120)
        if data is not None:
            cid = _id_dari(data)
            if cid:
                return cid, None
            last = {"status": 200, "detail": "respons tanpa id: " + json.dumps(data)[:200]}
        else:
            last = err
            # 401/403 = kunci salah → tidak ada gunanya mencoba bentuk lain.
            if (err or {}).get("status") in (401, 403):
                break
    return "", last


def _tunggu(config: dict | None, cid: str, timeout: float) -> tuple:
    """Polling sampai selesai. Kembalikan (status, data_terakhir)."""
    cfg = _cfg(config)
    jeda = max(1.0, float(cfg.get("pollSeconds") or 4))
    batas = time.time() + timeout
    terakhir = {}
    while time.time() < batas:
        data, _err = _req(config, "GET", f"/api/conversations/{cid}", timeout=30)
        if isinstance(data, dict):
            terakhir = data
            st = _status_dari(data)
            if st in _STATUS_SELESAI:
                return st, data
        time.sleep(jeda)
    return "timeout", terakhir


def _jawaban(config: dict | None, cid: str, terakhir: dict) -> str:
    data, _err = _req(config, "GET", f"/api/conversations/{cid}/agent_final_response", timeout=30)
    teks = _teks_dari(data) if data is not None else ""
    if teks:
        return teks
    return _teks_dari(terakhir)


def ask(prompt: str, config: dict | None = None, timeout: float | None = None,
        cwd: str | None = None) -> dict:
    """Jalankan satu tugas lewat engine OpenHands. {ok, text|error}."""
    cfg = _cfg(config)
    siap = ensure_stack(config)
    if not siap.get("ok"):
        return {"ok": False, "error": siap.get("error") or "stack agent-canvas tidak siap"}

    cid, err = _buat(config, prompt)
    if not cid:
        pesan = (err or {}).get("detail") or "gagal membuat percakapan"
        if (err or {}).get("status") in (401, 403):
            pesan = ("stack menolak kunci API. Pastikan stack dinyalakan oleh bot "
                     "(data/canvas.key) atau set LOCAL_BACKEND_API_KEY yang sama.")
        return {"ok": False, "error": str(pesan)[:400]}

    batas = float(timeout or cfg.get("timeoutSeconds") or 1800)
    st, terakhir = _tunggu(config, cid, batas)
    if st == "timeout":
        return {"ok": False, "conversation_id": cid,
                "error": f"tugas belum selesai dalam {int(batas)} detik (masih berjalan di canvas)"}
    teks = _jawaban(config, cid, terakhir)
    if st in _STATUS_GAGAL and not teks:
        return {"ok": False, "conversation_id": cid, "error": f"percakapan berakhir: {st}"}
    if not teks:
        return {"ok": False, "conversation_id": cid,
                "error": "percakapan selesai tanpa jawaban teks (lihat canvas)"}
    return {"ok": True, "text": teks, "conversation_id": cid, "status": st}


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------
def _main(argv: list) -> int:
    aksi = (argv[1] if len(argv) > 1 else "status").strip().lower()
    config = {}
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
        from agent.config import load_config  # type: ignore
        config = load_config()
    except Exception:
        config = {"root": os.getcwd()}

    if aksi == "status":
        print(json.dumps(status(config), ensure_ascii=False, indent=2))
        return 0
    if aksi == "start":
        print(json.dumps(ensure_stack(config), ensure_ascii=False, indent=2))
        return 0
    if aksi == "stop":
        print(json.dumps(stop(config), ensure_ascii=False, indent=2))
        return 0
    if aksi in ("ask", "run"):
        tugas = " ".join(argv[2:]).strip()
        if not tugas:
            print('pakai: python3 -m agent.canvas_engine ask "tugasnya"', file=sys.stderr)
            return 2
        hasil = ask(tugas, config)
        print(hasil.get("text") or hasil.get("error") or "")
        return 0 if hasil.get("ok") else 1
    print("aksi: status | start | stop | ask \"tugas\"", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv))
