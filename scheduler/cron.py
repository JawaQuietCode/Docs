"""cron.py — penjadwal tugas (cron 5-field) + penyimpanan job.

Node (transport) yang memegang socket WhatsApp, jadi eksekusi job ada di sisi
Node (`transport/scheduler.js`). Modul ini hanya mengurus:
  - parsing ekspresi cron 5-field standar (menit jam tgl bulan hari)
  - menghitung waktu jalan berikutnya
  - menyimpan job di `data/cron/jobs.json` (sumber kebenaran bersama Node)

Job berbentuk::

    {
      "id": "job_ab12cd",
      "when": "0 8 * * *",          # ekspresi cron
      "kind": "send" | "agent" | "plugin",
      "args": {...},                 # tergantung kind
      "enabled": true,
      "createdAt": 1791..., "lastRun": null, "nextRun": 1791...
    }

Format JSON dipilih (bukan SQLite) supaya Node bisa membacanya TANPA dependensi
tambahan, dan tulis bersifat atomik (tmp + rename).
"""
from __future__ import annotations

import json
import os
import random
import string
import time
from datetime import datetime, timezone
from pathlib import Path

FIELDS = [
    ("minute", 0, 59),
    ("hour", 0, 23),
    ("dom", 1, 31),
    ("month", 1, 12),
    ("dow", 0, 6),  # 0 = Minggu
]


class CronError(ValueError):
    pass


def _parse_field(spec: str, lo: int, hi: int) -> set:
    """Ubah satu field cron menjadi himpunan nilai yang sah."""
    spec = str(spec).strip()
    if not spec:
        raise CronError("field kosong")
    values: set = set()
    for part in spec.split(","):
        part = part.strip()
        if not part:
            raise CronError("bagian kosong")
        step = 1
        if "/" in part:
            base, _, step_s = part.partition("/")
            part = base.strip()
            try:
                step = int(step_s)
            except ValueError:
                raise CronError(f"langkah tidak sah: {step_s}")
            if step < 1:
                raise CronError("langkah harus >= 1")
        if part in ("*", ""):
            start, end = lo, hi
        elif "-" in part:
            a, _, b = part.partition("-")
            try:
                start, end = int(a), int(b)
            except ValueError:
                raise CronError(f"rentang tidak sah: {part}")
        else:
            try:
                start = end = int(part)
            except ValueError:
                raise CronError(f"nilai tidak sah: {part}")
        if start > end:
            raise CronError(f"rentang terbalik: {part}")
        for v in range(start, end + 1, step):
            if v < lo or v > hi:
                raise CronError(f"nilai {v} di luar rentang {lo}-{hi}")
            values.add(v)
    if not values:
        raise CronError("tidak ada nilai")
    return values


def parse(expr: str) -> list:
    """Parse ekspresi cron 5-field. Mengembalikan [set, set, set, set, set]."""
    parts = str(expr or "").split()
    if len(parts) != 5:
        raise CronError("cron harus 5 field: menit jam tgl bulan hari")
    return [_parse_field(p, lo, hi) for p, (_, lo, hi) in zip(parts, FIELDS)]


def next_run(expr: str, after: datetime | None = None) -> datetime:
    """Waktu jalan berikutnya setelah `after` (default: sekarang, waktu LOKAL).

    Memakai waktu lokal server (mis. Asia/Jakarta bila TZ diset) supaya
    '0 8 * * *' berarti pukul 08:00 waktu setempat, bukan UTC.

    Pencarian naif menit-per-menit, dibatasi ~366 hari ke depan.
    """
    sets = parse(expr)
    minute, hour, dom, month, dow = sets
    base = after or datetime.now()
    # Mulai dari menit berikutnya (buang detik).
    t = base.replace(second=0, microsecond=0)
    from datetime import timedelta
    t = t + timedelta(minutes=1)
    limit = t + timedelta(days=366)
    while t <= limit:
        # Python: weekday() Senin=0..Minggu=6 -> cron dow Minggu=0.
        cron_dow = (t.weekday() + 1) % 7
        if (
            t.minute in minute
            and t.hour in hour
            and t.month in month
            and cron_dow in dow
            and t.day in dom
        ):
            return t
        t = t + timedelta(minutes=1)
    raise CronError("tidak ada jadwal dalam 1 tahun ke depan")


def describe(expr: str) -> str:
    """Ringkasan manusiawi sederhana (untuk balasan bot)."""
    parts = str(expr or "").split()
    if len(parts) != 5:
        return expr
    m, h, dom, mon, dow = parts
    if m == "*" and h == "*":
        return "setiap menit"
    if h == "*" and m.isdigit():
        return f"setiap jam pada menit {m}"
    if m.isdigit() and h.isdigit():
        base = f"setiap hari pukul {int(h):02d}:{int(m):02d}"
        if dow != "*":
            names = ["Minggu", "Senin", "Selasa", "Rabu", "Kamis", "Jumat", "Sabtu"]
            try:
                days = [names[int(x) % 7] for x in dow.split(",")]
                base += " (" + ", ".join(days) + ")"
            except ValueError:
                pass
        return base
    return f"cron `{expr}`"


# ---------- penyimpanan job ----------

def _jobs_path(config: dict | None = None) -> Path:
    # Multi-tenant: bila ada tenant AKTIF, pakai file job MILIK tenant itu.
    try:
        from security.tenant_context import get_current
        cur = get_current() or {}
        if cur.get("cron"):
            return Path(cur["cron"])
    except Exception:
        pass
    sec = (config or {}).get("security", {}) or {}
    root = sec.get("workspaceDir") or "./workspace"
    # data/ sejajar dengan workspace
    base = Path(root).resolve().parent / "data" / "cron"
    return base / "jobs.json"


def _read_jobs(path: Path) -> list:
    try:
        raw = path.read_text(encoding="utf-8")
        data = json.loads(raw) if raw.strip() else []
        return data if isinstance(data, list) else []
    except FileNotFoundError:
        return []
    except Exception:
        return []


def _write_jobs(path: Path, jobs: list) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(jobs, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def new_id() -> str:
    return "job_" + "".join(random.choices(string.ascii_lowercase + string.digits, k=6))


def add_job(when: str, kind: str, args: dict, config: dict | None = None,
            enabled: bool = True) -> dict:
    """Tambah job baru. Melempar CronError bila ekspresi tidak sah."""
    nxt = next_run(when)
    path = _jobs_path(config)
    jobs = _read_jobs(path)
    tenant = ""
    try:
        from security.tenant_context import current_tenant
        tenant = current_tenant()
    except Exception:
        pass
    job = {
        "id": new_id(),
        "when": when,
        "kind": kind,
        "args": args or {},
        "tenant": tenant,
        "enabled": bool(enabled),
        "createdAt": int(time.time()),
        "lastRun": None,
        "nextRun": int(nxt.timestamp()),
    }
    jobs.append(job)
    _write_jobs(path, jobs)
    return job


def list_jobs(config: dict | None = None) -> list:
    return _read_jobs(_jobs_path(config))


def remove_job(job_id: str, config: dict | None = None) -> bool:
    path = _jobs_path(config)
    jobs = _read_jobs(path)
    keep = [j for j in jobs if j.get("id") != job_id]
    if len(keep) == len(jobs):
        return False
    _write_jobs(path, keep)
    return True


def set_enabled(job_id: str, enabled: bool, config: dict | None = None) -> dict | None:
    path = _jobs_path(config)
    jobs = _read_jobs(path)
    found = None
    for j in jobs:
        if j.get("id") == job_id:
            j["enabled"] = bool(enabled)
            if enabled:
                j["nextRun"] = int(next_run(j.get("when", "* * * * *")).timestamp())
            found = j
            break
    if found is not None:
        _write_jobs(path, jobs)
    return found
