"""context.py — konteks tenant AKTIF (per-permintaan).

FastAPI menjalankan tiap request di thread berbeda (def, bukan async), jadi
`contextvars` tidak otomatis terpropagasi ke thread pool. Karena itu kita pakai
`threading.local()`: setiap request menetapkan tenant aktif, dan tool yang
dipanggil di thread yang sama membacanya.

Yang disimpan: dict hasil ``memory.tenants.paths()`` (berisi db/cron/audit/
workspace). Tool (shell/files/cron/wa/subagent) memakai ini supaya bekerja di
folder tenant yang benar, bukan selalu folder global.
"""
from __future__ import annotations

import threading

_LOCAL = threading.local()


def set_current(tenant_paths: dict | None) -> None:
    _LOCAL.paths = tenant_paths or None


def get_current() -> dict | None:
    return getattr(_LOCAL, "paths", None)


def current_tenant() -> str:
    p = get_current() or {}
    return p.get("tenant") or ""


def current_workspace() -> str | None:
    p = get_current() or {}
    ws = p.get("workspace")
    return str(ws) if ws else None


def current_audit_path() -> str | None:
    p = get_current() or {}
    a = p.get("audit")
    return str(a) if a else None


def current_tasks_dir() -> str | None:
    """Folder tugas (checkpoint/resume) milik tenant aktif."""
    p = get_current() or {}
    t = p.get("tasks")
    return str(t) if t else None


def current_media_dir() -> str | None:
    """Folder penyimpanan media/asset milik tenant aktif."""
    p = get_current() or {}
    m = p.get("media")
    return str(m) if m else None


def clear() -> None:
    _LOCAL.paths = None
