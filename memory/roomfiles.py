"""roomfiles.py — MEKANISME MEMORY per ruangan (gaya hermes/claude/codex).

Tiap owner punya RUANGAN memori sendiri. Di dalam ruangan ada berkas memori
Markdown yang mengikuti gaya masing-masing mekanisme:

  native / hermes → MEMORY.md   (+ USER.md untuk hermes)
  claude          → CLAUDE.md
  codex           → AGENTS.md

Berkas-berkas ini adalah "memori jangka panjang" yang otomatis dibaca ke dalam
system prompt dan bisa ditulis oleh agent lewat tool ``memory``. Berbeda dari
tabel riwayat di SQLite (yang menyimpan percakapan mentah), berkas ini menyimpan
FAKTA/preferensi yang bertahan lintas sesi.

## Sejak kebijakan memori (Fitur #30)
Sumber kebenaran tiap berkas adalah **index JSON** di sebelahnya
(``<Nama>.index.json``); berkas ``.md`` menjadi **cermin** yang dirender ulang
dari index (hanya entri AKTIF yang tampil). Index memuat jenis entri, sumber,
waktu, status, dan kunci dedup — lihat ``memory/policy.py``.

Semua tulis-baca TERBATAS pada folder ruangan tenant (tidak menyentuh ruangan
owner lain), dan setiap path divalidasi tetap di bawah folder memori ruangan.
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path

from memory import policy
from memory import tenants

# Nama berkas memori per mekanisme.
_PRIMARY = {
    "native": "MEMORY.md",
    "hermes": "MEMORY.md",
    "claude": "CLAUDE.md",
    "codex": "AGENTS.md",
}
# Berkas tambahan per mekanisme (selalu ikut dibaca).
_EXTRA = {
    "hermes": ("USER.md",),
}

# SEMUA berkas memori yang mungkin ada di sebuah ruangan. Dipakai saat MEMBACA
# supaya memori tidak pernah hilang walau owner berganti mekanisme.
_ALL_NAMES = ("MEMORY.md", "USER.md", "CLAUDE.md", "AGENTS.md")

DEFAULT_MAX_CHARS = 8000

_TEMPLATES = {
    "MEMORY.md": (
        "# MEMORY\n\n"
        "> Memori jangka panjang. Tulis fakta/preferensi yang perlu diingat\n"
        "> lintas sesi. Satu fakta per baris, awali dengan `- `.\n\n"
        "## Fakta\n\n"
        "## Preferensi\n\n"
    ),
    "USER.md": (
        "# USER\n\n"
        "> Profil pemilik: nama, gaya bahasa, kebiasaan.\n\n"
    ),
    "CLAUDE.md": (
        "# CLAUDE\n\n"
        "> Konteks proyek & memori. Instruksi/fakta yang perlu selalu diingat.\n\n"
        "## Memori\n\n"
        "## Aturan kerja\n\n"
    ),
    "AGENTS.md": (
        "# AGENTS\n\n"
        "> Panduan & memori agent. Fakta yang perlu diingat lintas sesi.\n\n"
        "## Memori\n\n"
        "## Aturan\n\n"
    ),
}

# Judul cermin per nama berkas.
_MIRROR_TITLE = {
    "MEMORY.md": "MEMORY",
    "USER.md": "USER",
    "CLAUDE.md": "CLAUDE",
    "AGENTS.md": "AGENTS",
}

# Baris cermin yang bisa diimpor saat migrasi: "- [F] teks · src:user · 2026-10-10"
_MIRROR_LINE_RE = re.compile(r"^\s*-\s*(?:\[(?P<tag>[FPDG])\]\s*)?(?P<text>.+?)\s*$")
_META_SPLIT_RE = re.compile(r"\s+·\s+")
_TAG_TO_KIND = {"F": policy.FACT, "P": policy.PREFERENCE,
                "D": policy.DECISION, "G": policy.GUESS}


def memory_dir(room: dict) -> Path:
    """Folder memori di dalam sebuah ruangan."""
    md = room.get("memdir")
    if md:
        return Path(md)
    return Path(room["root"]) / "memory" / "notes"


def primary_file(mechanism) -> str:
    return _PRIMARY.get(tenants.normalize_mechanism(mechanism), "MEMORY.md")


def file_names(mechanism) -> list:
    """Nama berkas memori GAYA mekanisme ini (yang dibaca & ditulis)."""
    m = tenants.normalize_mechanism(mechanism)
    out = [primary_file(m)]
    for extra in _EXTRA.get(m, ()):
        if extra not in out:
            out.append(extra)
    return out


# --- Konfinasi path --------------------------------------------------------

def _confined(room: dict, name: str) -> Path | None:
    """Path berkas memori yang PASTI di dalam folder memori ruangan."""
    d = memory_dir(room)
    target = policy.confine(d, d / str(name))
    return target


def _index_path(room: dict, name: str) -> Path | None:
    d = memory_dir(room)
    return policy.confine(d, d / f"{name}.index.json")


# --- Index -----------------------------------------------------------------

def _load_index(room: dict, name: str) -> dict:
    """Baca index JSON; kosong bila belum ada. Impor cermin lama bila perlu."""
    p = _index_path(room, name)
    if p is None:
        return {}
    try:
        if p.exists():
            data = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return {str(k): v for k, v in data.items() if isinstance(v, dict)}
    except Exception:
        pass
    return {}


def _save_index(room: dict, name: str, index: dict) -> bool:
    p = _index_path(room, name)
    if p is None:
        return False
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(p.suffix + ".tmp")
        tmp.write_text(json.dumps(index, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(p)
        return True
    except Exception:
        return False


def _migrate(room: dict, name: str, index: dict) -> dict:
    """Impor baris ``- …`` dari cermin lama ke index (sekali, saat index kosong).

    Entri lama menjadi ``kind=fact``, ``source=unknown``, tanpa waktu — supaya
    memori yang sudah ada TIDAK hilang saat kebijakan baru dipasang.
    """
    if index:
        return index
    p = _confined(room, name)
    if p is None or not p.exists():
        return index
    try:
        txt = p.read_text(encoding="utf-8")
    except Exception:
        return index
    for line in txt.splitlines():
        m = _MIRROR_LINE_RE.match(line)
        if not m:
            continue
        text = m.group("text").strip()
        if not text or text.startswith("#") or text.startswith(">"):
            continue
        kind = _TAG_TO_KIND.get(m.group("tag") or "F", policy.FACT)
        parts = _META_SPLIT_RE.split(text)
        body = parts[0].strip()
        src = "unknown"
        for extra in parts[1:]:
            if extra.startswith("src:"):
                src = extra[4:].strip() or "unknown"
        key = policy.norm_key(body)
        if key in index:
            continue
        index[key] = {
            "id": policy.new_id(),
            "kind": kind,
            "text": body,
            "norm_key": key,
            "source": policy.normalize_source(src),
            "source_ref": "",
            "created_ts": 0,
            "updated_ts": 0,
            "expires_ts": 0,
            "status": policy.STATUS_ACTIVE,
        }
    return index


def _active_entries(index: dict, now: int | None = None) -> list:
    """Entri aktif; entri kedaluwarsa ditandai stale (lazy, tanpa timer)."""
    out = []
    changed = False
    for e in index.values():
        if str(e.get("status") or policy.STATUS_ACTIVE) != policy.STATUS_ACTIVE:
            continue
        if policy.is_expired(e, now):
            e["status"] = policy.STATUS_STALE
            changed = True
            continue
        out.append(e)
    return out, changed


def _write_mirror(room: dict, name: str, index: dict) -> bool:
    p = _confined(room, name)
    if p is None:
        return False
    active = [e for e in index.values()
              if str(e.get("status") or policy.STATUS_ACTIVE) == policy.STATUS_ACTIVE]
    active.sort(key=lambda e: int(e.get("updated_ts") or e.get("created_ts") or 0))
    txt = policy.render_mirror(active, title=_MIRROR_TITLE.get(name, "MEMORY"))
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(txt, encoding="utf-8")
        return True
    except Exception:
        return False


def _load_ready(room: dict, mechanism) -> tuple[str, dict, bool]:
    """Muat index berkas utama (+ migrasi). Kembalikan (nama, index, berubah)."""
    name = primary_file(mechanism)
    idx = _load_index(room, name)
    before = len(idx)
    idx = _migrate(room, name, idx)
    return name, idx, len(idx) != before


def ensure_room_files(room: dict, mechanism) -> list:
    """Buat berkas memori (dengan template) bila belum ada. Kembalikan path-nya."""
    d = memory_dir(room)
    made = []
    try:
        d.mkdir(parents=True, exist_ok=True)
    except Exception:
        return []
    for name in file_names(mechanism):
        p = _confined(room, name)
        if p is None:
            continue
        if not p.exists():
            try:
                p.write_text(_TEMPLATES.get(name, f"# {name}\n\n"), encoding="utf-8")
            except Exception:
                continue
        made.append(p)
    return made


def _read(path: Path, max_chars: int) -> str:
    try:
        if not path.exists():
            return ""
        txt = path.read_text(encoding="utf-8")
    except Exception:
        return ""
    if len(txt) > max_chars:
        txt = txt[:max_chars] + "\n… [memori dipotong]"
    return txt


def read_memory(room: dict, mechanism, max_chars: int = DEFAULT_MAX_CHARS) -> str:
    """Gabungkan isi berkas memori ruangan (yang tidak kosong)."""
    d = memory_dir(room)
    chunks = []
    for name in file_names(mechanism):
        txt = _read(d / name, max_chars).strip()
        if txt:
            chunks.append(f"<!-- {name} -->\n{txt}")
    return "\n\n".join(chunks)


# --- Operasi entri ---------------------------------------------------------

def add_entry(room: dict, mechanism, text: str, kind: str = policy.FACT,
              source: str = "user", source_ref: str = "",
              ttl_days: int | None = None, allow_sensitive: bool = False) -> dict:
    """Tambah satu entri. Idempoten (dedup lewat teks ter-normalisasi)."""
    err = policy.relevance_error(text)
    if err:
        return {"ok": False, "error": err}
    why = policy.secret_reason(text)
    if why and not allow_sensitive:
        return {"ok": False, "error": f"ditolak: tampak rahasia ({why})", "secret": True}

    name, index, _ = _load_ready(room, mechanism)
    ensure_room_files(room, mechanism)
    now = int(time.time())
    key = policy.norm_key(text)
    clean = " ".join(str(text).split())

    # Dedup: entri dengan kunci sama → perbarui, jangan gandakan.
    if key in index:
        e = index[key]
        if str(e.get("status")) == policy.STATUS_ACTIVE:
            e["updated_ts"] = now
            e["kind"] = policy.normalize_kind(kind or e.get("kind"))
            if source_ref:
                e["source_ref"] = source_ref
            _save_index(room, name, index)
            _write_mirror(room, name, index)
            return {"ok": True, "duplicate": True, "id": e["id"], "kind": e["kind"]}
        # pernah dilupakan/di-stale → hidupkan kembali.
        e.update({"status": policy.STATUS_ACTIVE, "text": clean,
                  "kind": policy.normalize_kind(kind), "updated_ts": now})
        _save_index(room, name, index)
        _write_mirror(room, name, index)
        return {"ok": True, "id": e["id"], "kind": e["kind"]}

    # Batas ruangan: evakuasi entri terlemah bila sudah penuh.
    active, _ = _active_entries(index, now)
    if len(active) >= policy.MAX_ACTIVE:
        _evict_one(index, now)

    eid = policy.new_id()
    index[key] = {
        "id": eid,
        "kind": policy.normalize_kind(kind),
        "text": clean,
        "norm_key": key,
        "source": policy.normalize_source(source),
        "source_ref": str(source_ref or ""),
        "created_ts": now,
        "updated_ts": now,
        "expires_ts": policy.expires_ts(kind, now, ttl_days),
        "status": policy.STATUS_ACTIVE,
    }
    _save_index(room, name, index)
    _write_mirror(room, name, index)
    return {"ok": True, "id": eid, "kind": index[key]["kind"]}


def _evict_one(index: dict, now: int) -> None:
    """Tandai satu entri terlemah sebagai stale (dugaan → keputusan → preferensi)."""
    for kind in policy.EVICT_ORDER:
        cand = [e for e in index.values()
                if str(e.get("status")) == policy.STATUS_ACTIVE
                and policy.normalize_kind(e.get("kind")) == kind]
        if cand:
            cand.sort(key=lambda e: int(e.get("updated_ts") or e.get("created_ts") or 0))
            cand[0]["status"] = policy.STATUS_STALE
            return


def _find(index: dict, ref: str) -> tuple[str | None, dict | None]:
    """Cari entri lewat id ATAU teks ter-normalisasi."""
    ref = str(ref or "").strip()
    if not ref:
        return None, None
    for k, e in index.items():
        if e.get("id") == ref:
            return k, e
    key = policy.norm_key(ref)
    if key in index:
        return key, index[key]
    return None, None


def update_entry(room: dict, mechanism, ref: str, text: str | None = None,
                 kind: str | None = None, source: str | None = None,
                 allow_sensitive: bool = False) -> dict:
    """Perbarui entri (id ATAU teks lama). Teks baru harus lolos gerbang."""
    name, index, _ = _load_ready(room, mechanism)
    key, e = _find(index, ref)
    if e is None:
        return {"ok": False, "error": "entri tidak ditemukan"}
    if text is not None:
        err = policy.relevance_error(text)
        if err:
            return {"ok": False, "error": err}
        why = policy.secret_reason(text)
        if why and not allow_sensitive:
            return {"ok": False, "error": f"ditolak: tampak rahasia ({why})", "secret": True}
        newtext = " ".join(str(text).split())
        newkey = policy.norm_key(newtext)
        if newkey != key and newkey in index:
            return {"ok": False, "error": "sudah ada entri dengan isi sama", "duplicate": True}
        if newkey != key:
            index.pop(key, None)
            key = newkey
            e["norm_key"] = newkey
        e["text"] = newtext
    if kind:
        e["kind"] = policy.normalize_kind(kind)
        e["expires_ts"] = policy.expires_ts(e["kind"], int(time.time()), None)
    if source:
        e["source"] = policy.normalize_source(source)
    e["updated_ts"] = int(time.time())
    e["status"] = policy.STATUS_ACTIVE
    index[key] = e
    _save_index(room, name, index)
    _write_mirror(room, name, index)
    return {"ok": True, "id": e["id"], "kind": e["kind"]}


def forget_entry(room: dict, mechanism, ref: str, reason: str = "") -> dict:
    """Lupakan (soft): entri hilang dari cermin & prompt; teks dibuang (tombstone)."""
    name, index, _ = _load_ready(room, mechanism)
    key, e = _find(index, ref)
    if e is None:
        return {"ok": False, "error": "entri tidak ditemukan"}
    e["status"] = policy.STATUS_FORGOTTEN
    e["text"] = ""
    e["forgotten_ts"] = int(time.time())
    e["reason"] = str(reason or "")
    e["updated_ts"] = int(time.time())
    _save_index(room, name, index)
    _write_mirror(room, name, index)
    return {"ok": True, "id": e["id"], "kind": e["kind"]}


def delete_entry(room: dict, mechanism, ref: str) -> dict:
    """Hapus (hard): entri dibuang total dari index & cermin."""
    name, index, _ = _load_ready(room, mechanism)
    key, e = _find(index, ref)
    if e is None:
        return {"ok": False, "error": "entri tidak ditemukan"}
    index.pop(key, None)
    _save_index(room, name, index)
    _write_mirror(room, name, index)
    return {"ok": True, "id": e.get("id"), "kind": e.get("kind")}


def list_entries(room: dict, mechanism, kind: str | None = None,
                 include_stale: bool = False, limit: int = 200) -> list:
    """Daftar entri ruangan (hanya AKTIF kecuali ``include_stale``)."""
    name, index, _ = _load_ready(room, mechanism)
    active, changed = _active_entries(index, int(time.time()))
    if changed:
        _save_index(room, name, index)
        _write_mirror(room, name, index)
    want = policy.normalize_kind(kind) if kind else None
    out = []
    for e in index.values():
        st = str(e.get("status") or policy.STATUS_ACTIVE)
        if st == policy.STATUS_FORGOTTEN:
            continue
        if st != policy.STATUS_ACTIVE and not include_stale:
            continue
        if want and policy.normalize_kind(e.get("kind")) != want:
            continue
        out.append({
            "id": e.get("id"), "kind": policy.normalize_kind(e.get("kind")),
            "text": e.get("text", ""), "source": e.get("source", "unknown"),
            "status": st, "updated_ts": e.get("updated_ts", 0),
        })
        if len(out) >= limit:
            break
    return out


def search_entries(room: dict, mechanism, query: str, include_stale: bool = False,
                   limit: int = 20) -> list:
    """Cari entri AKTIF yang memuat ``query`` (case-insensitive)."""
    q = str(query or "").strip().lower()
    if not q:
        return []
    out = []
    for e in list_entries(room, mechanism, include_stale=include_stale, limit=100000):
        if q in str(e.get("text", "")).lower():
            out.append(e)
            if len(out) >= limit:
                break
    return out


# --- API lama (kompatibilitas) ---------------------------------------------

def append_memory(room: dict, mechanism, text: str, section: str = "Fakta") -> dict:
    """Tambah satu fakta (API lama). Kini lewat index + cermin (kebijakan baru)."""
    kind = policy.FACT
    sec = str(section or "").strip().lower()
    if sec.startswith("prefer"):
        kind = policy.PREFERENCE
    elif sec.startswith("keputusan") or sec.startswith("decision"):
        kind = policy.DECISION
    elif sec.startswith("dugaan") or sec.startswith("guess"):
        kind = policy.GUESS
    return add_entry(room, mechanism, text, kind=kind, source="user")


def search_memory(room: dict, mechanism, query: str, limit: int = 20) -> list:
    """Cari baris memori (API lama) — dibungkus dari ``search_entries``."""
    return [{"file": primary_file(mechanism), "line": e["text"]}
            for e in search_entries(room, mechanism, query, limit=limit)]


def list_facts(room: dict, mechanism, limit: int = 50) -> list:
    """Daftar fakta (API lama) — dibungkus dari ``list_entries``."""
    return [{"file": primary_file(mechanism), "line": e["text"]}
            for e in list_entries(room, mechanism, limit=limit)]
