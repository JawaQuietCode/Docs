"""tenants.py — registri multi-owner (multi-tenant) + tata letak path.

Konsep: SETIAP owner adalah satu **tenant** dengan miliknya SENDIRI:

  - DB (riwayat + profil + izin) : data/tenants/<t>/memory/kwee.db
  - sesi/workspace               : data/tenants/<t>/workspace
  - tugas terjadwal (cron)       : data/tenants/<t>/cron/jobs.json
  - audit                        : data/tenants/<t>/audit.log
  - buffer perintah cepat        : data/tenants/<t>/cmdbuf

Owner UTAMA (primary) sengaja memakai **path lama** (data/memory/kwee.db,
data/cron/jobs.json, data/audit.log, workspace/) supaya riwayat & perilaku
yang sudah ada TIDAK hilang. Ia juga super-owner: boleh mengelola semua tenant.

Tenant id = nomor telepon owner (kanonik), stabil lintas LID/PN sehingga
pemetaan LID<->PN di transport cukup untuk menemukannya.
"""
from __future__ import annotations

import re
from pathlib import Path

from security.allowlist import candidates, owner_matches, typed_candidates
from security.lidmap import expand

TENANTS_SUBDIR = ("data", "tenants")


def _digits(value) -> str:
    """Ambil HANYA angka dari sebuah nilai identitas (nomor telepon/LID).

    Tangguh terhadap format umum: ``+62 831-946-78090`` → ``6283194678090``.
    """
    return re.sub(r"\D", "", str(value or ""))


def normalize(tenant) -> str:
    """Tenant id kanonik = angka saja."""
    return _digits(tenant)


def _norm_numbers(values) -> list:
    """Daftar nomor owner ternormalisasi, urut & unik (pertahankan urutan)."""
    out: list = []
    for v in (values or []):
        n = _digits(v)
        if n and n not in out:
            out.append(n)
    return out


def owner_numbers(config: dict | None = None) -> list:
    """Semua nomor owner yang terdaftar (dari WA_OWNER_NUMBERS)."""
    return _norm_numbers((config or {}).get("owner") or [])


def primary_tenant(config: dict | None = None) -> str:
    """Tenant owner UTAMA (super-owner).

    Urutan: ``primaryOwner`` eksplisit → owner pertama → string kosong.
    """
    cfg = config or {}
    explicit = _digits(cfg.get("primaryOwner") or "")
    if explicit:
        return explicit
    owners = owner_numbers(cfg)
    return owners[0] if owners else ""


def is_primary(tenant: str, config: dict | None = None) -> bool:
    t = normalize(tenant or "")
    return bool(t) and t == primary_tenant(config)


def is_owner_tenant(tenant: str, config: dict | None = None) -> bool:
    return normalize(tenant or "") in set(owner_numbers(config))


def resolve_tenant(sender_ids, config: dict | None = None, lid_dir=None) -> str | None:
    """Pilih tenant (nomor owner) dari identitas pengirim.

    Mengembalikan nomor owner yang cocok, atau None bila pengirim bukan owner.

    PENTING — pencocokan **sadar-tipe**: nomor telepon dan LID berbagi ruang
    angka (angka LID seseorang bisa sama dengan nomor telepon orang lain), jadi
    token PN hanya dicocokkan dengan nomor owner, dan token LID hanya lewat
    pemetaan ``lid2pn`` yang sah. Tanpa ini, dua owner bisa bertukar identitas
    dan memakai DB satu sama lain.
    """
    typed = typed_candidates(sender_ids)
    if not typed:
        return None
    for owner in owner_numbers(config):
        if owner_matches(typed, owner, lid_dir):
            return owner
    return None


def tenants_dir(config: dict | None = None) -> Path:
    root = Path((config or {}).get("root") or ".").resolve()
    return root.joinpath(*TENANTS_SUBDIR)


def tenant_root(tenant: str, config: dict | None = None) -> Path:
    return tenants_dir(config) / normalize(tenant or "")


def paths(tenant: str, config: dict | None = None) -> dict:
    """Tata letak path untuk sebuah tenant.

    Tenant utama memakai path LAMA (kompatibel); tenant lain memakai folder
    terisolasi di bawah ``data/tenants/<t>/``.
    """
    cfg = config or {}
    t = normalize(tenant or "")
    root = Path(cfg.get("root") or ".").resolve()
    if is_primary(t, cfg):
        return {
            "tenant": t,
            "primary": True,
            "root": root,
            "db": root / "data" / "memory" / "kwee.db",
            "sessions": root / "data" / "sessions",
            "memdir": root / "data" / "memory" / "notes",
            "cron": root / "data" / "cron" / "jobs.json",
            "audit": root / "data" / "audit.log",
            "cmdbuf": root / "data" / "cmdbuf",
            "tasks": root / "data" / "tasks",
            "media": root / "data" / "media",
            "workspace": root / "workspace",
        }
    base = tenant_root(t, cfg)
    return {
        "tenant": t,
        "primary": False,
        "root": base,
        "db": base / "memory" / "kwee.db",
        "sessions": base / "sessions",
        "memdir": base / "memory" / "notes",
        "cron": base / "cron" / "jobs.json",
        "audit": base / "audit.log",
        "cmdbuf": base / "cmdbuf",
        "tasks": base / "tasks",
        "media": base / "media",
        "workspace": base / "workspace",
    }


def ensure_tenant(tenant: str, config: dict | None = None) -> dict:
    """Buat folder tenant bila belum ada. Mengembalikan dict path-nya."""
    p = paths(tenant, config)
    try:
        p["workspace"].mkdir(parents=True, exist_ok=True)
        p["db"].parent.mkdir(parents=True, exist_ok=True)
        p["sessions"].mkdir(parents=True, exist_ok=True)
        p["cron"].parent.mkdir(parents=True, exist_ok=True)
        p["cmdbuf"].mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    return p


# --- MEKANISME (memory) per owner ------------------------------------------
# Tiap owner = SATU ruangan memori (satu DB + satu folder memori) supaya antar
# owner tidak bertabrakan. MEKANISME hanya menentukan GAYA/NAMA berkas memori di
# dalam ruangan itu (MEMORY.md / CLAUDE.md / AGENTS.md) — BUKAN folder terpisah.
VALID_MECHANISMS = ("native", "hermes", "claude", "codex")
_MECH_ALIASES = {
    "": "native", "kwee": "native", "bawaan": "native", "default": "native",
    "hermes": "hermes", "hermes-ai": "hermes", "hermes_ai": "hermes",
    "claude": "claude", "claude-code": "claude", "claudecode": "claude", "cc": "claude",
    "codex": "codex", "openai-codex": "codex",
    # OpenHands lewat @openhands/agent-canvas (stack HTTP, bukan CLI argv).
    "canvas": "canvas", "agent-canvas": "canvas", "agent_canvas": "canvas",
    "openhands": "canvas", "oh": "canvas",
}


def normalize_mechanism(name) -> str:
    """Nama mekanisme kanonik (fallback: native)."""
    key = str(name or "").strip().lower()
    return _MECH_ALIASES.get(key, "native")


def mechanism_for(tenant: str, config: dict | None = None) -> str:
    """Mekanisme yang dipakai sebuah tenant.

    Urutan: peta ``ownerMechanisms`` (per-owner) → ``defaultMechanism`` → native.
    """
    cfg = config or {}
    t = normalize(tenant)
    mapping = cfg.get("ownerMechanisms") or {}
    if t and isinstance(mapping, dict) and mapping.get(t):
        return normalize_mechanism(mapping[t])
    return normalize_mechanism(cfg.get("defaultMechanism") or "native")


def room_paths(tenant: str, mechanism=None, config: dict | None = None) -> dict:
    """RUANGAN milik seorang owner: SATU DB + SATU folder memori.

    Mekanisme TIDAK memecah ruangan — ia hanya menentukan nama berkas memori
    (lihat ``memory/roomfiles.py``). Jadi satu owner selalu punya satu memori dan
    satu DB, tidak peduli mekanisme yang dipakai.
    """
    cfg = config or {}
    t = normalize(tenant)
    m = normalize_mechanism(mechanism if mechanism is not None else mechanism_for(t, cfg))
    out = dict(paths(t, cfg))
    out["mechanism"] = m
    return out


def ensure_room(tenant: str, mechanism=None, config: dict | None = None) -> dict:
    """Buat folder ruangan owner bila belum ada."""
    p = room_paths(tenant, mechanism, config)
    try:
        p["workspace"].mkdir(parents=True, exist_ok=True)
        p["db"].parent.mkdir(parents=True, exist_ok=True)
        p["sessions"].mkdir(parents=True, exist_ok=True)
        p["memdir"].mkdir(parents=True, exist_ok=True)
        p["cron"].parent.mkdir(parents=True, exist_ok=True)
        p["cmdbuf"].mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    return p


def list_tenants(config: dict | None = None) -> list:
    """Daftar tenant: owner terdaftar + folder tenant yang ada di disk."""
    cfg = config or {}
    seen: list = []
    for owner in owner_numbers(cfg):
        if owner not in seen:
            seen.append(owner)
    try:
        base = tenants_dir(cfg)
        if base.is_dir():
            for d in sorted(base.iterdir()):
                if d.is_dir() and d.name.isdigit() and d.name not in seen:
                    seen.append(d.name)
    except Exception:
        pass
    out = []
    for t in seen:
        p = paths(t, cfg)
        out.append({
            "id": t,
            "primary": bool(p["primary"]),
            "registered": t in owner_numbers(cfg),
            "root": str(p["root"]),
            "db": str(p["db"]),
        })
    return out
