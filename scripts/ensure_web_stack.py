#!/usr/bin/env python3
"""ensure_web_stack.py — pastikan FastAPI + Starlette + Uvicorn SALING COCOK.

Latar belakang (error di panel):

    File ".../fastapi/applications.py", line 896, in __init__
        ] = webhooks or routing.APIRouter()
    File ".../fastapi/routing.py", line 835, in __init__
        super().__init__(

`fastapi==0.115.0` hanya cocok dengan `starlette<0.39`. Paket yang dipasang
belakangan oleh `setup-intelligence.sh` (mcp, graphifyy[mcp], langchain, ...)
menarik Starlette naik ke versi baru yang sudah tidak menerima argumen
`on_startup` / `on_shutdown`. pip hanya MENGETIK peringatan "incompatible",
tidak membatalkan — sehingga `FastAPI()` meledak saat import `agent.main`
dan agent mati sebelum health check.

Skrip ini memeriksa kecocokan dengan benar-benar membuat `FastAPI()` di
subproses, lalu memperbaikinya bila gagal:

  1. `pip install --upgrade fastapi starlette uvicorn` (resolver memilih
     pasangan yang saling cocok — jalur utama, ikut versi terbaru).
  2. Cadangan: pin klasik `fastapi==0.115.0` + `starlette>=0.37.2,<0.39`.

Exit code 0 = siap; 1 = tetap rusak (pesan jelas di stderr).
Dipanggil dari start.sh setelah semua pemasangan paket selesai.
"""
from __future__ import annotations

import subprocess
import sys

PROBE = r"""
import sys
from fastapi import FastAPI, APIRouter
from contextlib import asynccontextmanager

@asynccontextmanager
async def _ls(_app):
    yield

APIRouter()
FastAPI(title="probe", lifespan=_ls)
import fastapi, starlette, uvicorn
print(f"fastapi {fastapi.__version__} / starlette {starlette.__version__} / uvicorn {uvicorn.__version__}")
"""


def probe() -> tuple[bool, str]:
    proc = subprocess.run([sys.executable, "-c", PROBE], capture_output=True, text=True)
    out = (proc.stdout or "").strip()
    err = (proc.stderr or "").strip().splitlines()
    return proc.returncode == 0, out if proc.returncode == 0 else (err[-1] if err else "gagal tanpa pesan")


def pip(*args: str) -> bool:
    cmd = [sys.executable, "-m", "pip", "install", "--user", "--break-system-packages",
           "--disable-pip-version-check", "--quiet", *args]
    return subprocess.run(cmd).returncode == 0


def main() -> int:
    ok, info = probe()
    if ok:
        print(f"[web-stack] OK — {info}")
        return 0
    print(f"[web-stack] FastAPI/Starlette tidak cocok: {info}", file=sys.stderr)

    print("[web-stack] memperbaiki: upgrade fastapi + starlette + uvicorn ke pasangan yang cocok", file=sys.stderr)
    pip("--upgrade", "fastapi", "starlette", "uvicorn")
    ok, info = probe()
    if ok:
        print(f"[web-stack] diperbaiki — {info}")
        return 0

    print(f"[web-stack] masih gagal ({info}); mencoba pin klasik", file=sys.stderr)
    pip("--force-reinstall", "fastapi==0.115.0", "starlette>=0.37.2,<0.39.0", "uvicorn==0.30.6")
    ok, info = probe()
    if ok:
        print(f"[web-stack] diperbaiki (pin klasik) — {info}")
        return 0

    print(f"[web-stack] GAGAL memperbaiki otomatis: {info}\n"
          "  Jalankan manual: python3 -m pip install --user --break-system-packages "
          "--force-reinstall 'fastapi==0.115.0' 'starlette>=0.37.2,<0.39.0' 'uvicorn==0.30.6'",
          file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
