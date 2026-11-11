"""policy.py — kebijakan memori lintas sesi (jenis, TTL, dedup, rahasia, path).

Modul ini adalah SATU tempat untuk aturan yang dipakai lapisan memori:

- **Jenis entri**: ``fact`` (terverifikasi), ``preference``, ``decision``
  (keputusan sementara), ``guess`` (dugaan). Jenis menentukan TTL & tag render.
- **Retensi**: fact/preference tak kedaluwarsa; decision 30 hari; guess 7 hari.
  Entri kedaluwarsa ditandai ``stale`` (tetap tersimpan, tidak dirender ke prompt).
- **Batas ruangan**: maksimum entri aktif per berkas.
- **Rahasia**: gerbang yang MENOLAK teks yang tampak seperti token/key/password/
  JWT dsb. Penyimpanan rahasia hanya mungkin dengan izin eksplisit.
- **Konfinasi path**: berkas memori WAJIB berada di bawah folder memori tenant.
- **Relevansi**: teks terlalu pendek / pertanyaan / hampa ditolak.

Modul ini MURNI (tanpa I/O & tanpa menyentuh data produksi) supaya mudah diuji.
"""
from __future__ import annotations

import hashlib
import re
import time
from pathlib import Path

# --- Jenis entri -----------------------------------------------------------

FACT = "fact"
PREFERENCE = "preference"
DECISION = "decision"
GUESS = "guess"

KINDS = (FACT, PREFERENCE, DECISION, GUESS)

# Tag ringkas untuk cermin Markdown.
KIND_TAG = {FACT: "F", PREFERENCE: "P", DECISION: "D", GUESS: "G"}
# Label bagian (heading) per jenis di cermin.
KIND_SECTION = {
    FACT: "Fakta",
    PREFERENCE: "Preferensi",
    DECISION: "Keputusan",
    GUESS: "Dugaan",
}
# TTL bawaan (hari). 0 = tak kedaluwarsa.
KIND_TTL_DAYS = {FACT: 0, PREFERENCE: 0, DECISION: 30, GUESS: 7}

# Sumber yang sah.
SOURCES = ("user", "observed", "inferred", "unknown")

# Batas ruangan.
MAX_ACTIVE = 200
MAX_TEXT_CHARS = 500
MIN_TEXT_CHARS = 3

# Status entri.
STATUS_ACTIVE = "active"
STATUS_STALE = "stale"
STATUS_FORGOTTEN = "forgotten"

# Urutan evakuasi saat ruangan penuh (jenis yang lebih lemah dulu).
EVICT_ORDER = (GUESS, DECISION, PREFERENCE)


def normalize_kind(value) -> str:
    """Nama jenis kanonik; tak dikenal → ``fact``."""
    k = str(value or "").strip().lower()
    aliases = {
        "fakta": FACT, "f": FACT, "fact": FACT,
        "preferensi": PREFERENCE, "pref": PREFERENCE, "preference": PREFERENCE, "p": PREFERENCE,
        "keputusan": DECISION, "decision": DECISION, "d": DECISION, "sementara": DECISION,
        "dugaan": GUESS, "guess": GUESS, "g": GUESS, "asumsi": GUESS,
    }
    return aliases.get(k, FACT)


def normalize_source(value) -> str:
    s = str(value or "").strip().lower()
    return s if s in SOURCES else "unknown"


# --- Normalisasi & dedup ---------------------------------------------------

_WS_RE = re.compile(r"\s+")
_EDGE_PUNCT = " \t\r\n.,;:!?\"'`()[]{}<>-–—•*_~"


def normalize_text(text: str) -> str:
    """Bentuk teks untuk pembandingan: spasi rapat, huruf kecil, tanda tepi dibuang."""
    t = _WS_RE.sub(" ", str(text or "")).strip()
    return t.strip(_EDGE_PUNCT).lower()


def norm_key(text: str) -> str:
    """Kunci dedup stabil (sha1 teks ter-normalisasi)."""
    return hashlib.sha1(normalize_text(text).encode("utf-8")).hexdigest()


def new_id() -> str:
    """Id entri ringkas (8 hex)."""
    import os
    return os.urandom(4).hex()


# --- Relevansi -------------------------------------------------------------

def relevance_error(text: str) -> str | None:
    """Kembalikan alasan bila teks TIDAK layak disimpan, atau None bila layak."""
    t = _WS_RE.sub(" ", str(text or "")).strip()
    if not t:
        return "teks kosong"
    if len(t) < MIN_TEXT_CHARS:
        return "teks terlalu pendek"
    if t.endswith("?"):
        return "pertanyaan bukan fakta"
    # Hanya tanda baca / simbol (tak ada huruf atau angka).
    if not re.search(r"[0-9A-Za-z]", t):
        return "tak ada isi bermakna"
    if len(t) > MAX_TEXT_CHARS:
        return "teks terlalu panjang"
    return None


# --- Rahasia ---------------------------------------------------------------

# Pola yang JELAS rahasia (token/key/secret). Sengaja konservatif.
_SECRET_PATTERNS = (
    (re.compile(r"\bptla_[A-Za-z0-9]{16,}"), "application key panel"),
    (re.compile(r"\bptlc_[A-Za-z0-9]{16,}"), "client key panel"),
    (re.compile(r"\bsk-[A-Za-z0-9_\-]{16,}"), "API key (sk-)"),
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "AWS access key"),
    (re.compile(r"\bAIza[0-9A-Za-z_\-]{20,}"), "Google API key"),
    (re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}"), "GitHub token"),
    (re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}"), "Slack token"),
    (re.compile(r"\beyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{6,}"), "JWT"),
    (re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._\-]{20,}"), "bearer token"),
    (re.compile(r"(?i)\b(?:password|passwd|passphrase|secret|api[_-]?key|apikey|"
                r"access[_-]?token|auth[_-]?token|client[_-]?secret|credential)s?\b"
                r"\s*[:=]\s*\S{4,}"), "kredensial (key=value)"),
    (re.compile(r"(?i)\bBEGIN\s+(?:RSA|OPENSSH|PRIVATE|EC|PGP)\b.*\bKEY\b"), "kunci privat"),
    # Blob panjang tanpa spasi (base64/hex) — indikasi kuat token mentah.
    (re.compile(r"(?<!\S)[A-Za-z0-9+/]{40,}={0,2}(?!\S)"), "blob token panjang"),
)


def secret_reason(text: str) -> str | None:
    """Kembalikan alasan bila teks tampak rahasia, atau None bila aman."""
    s = str(text or "")
    for rx, why in _SECRET_PATTERNS:
        if rx.search(s):
            return why
    return None


def is_secret(text: str) -> bool:
    return secret_reason(text) is not None


# --- Waktu & TTL -----------------------------------------------------------

def expires_ts(kind: str, now: int | None = None, ttl_days: int | None = None) -> int:
    """Cap waktu kedaluwarsa (epoch detik). 0 = tak kedaluwarsa."""
    k = normalize_kind(kind)
    days = KIND_TTL_DAYS.get(k, 0) if ttl_days is None else int(ttl_days)
    if not days or days <= 0:
        return 0
    return int(now if now is not None else time.time()) + int(days) * 86400


def is_expired(entry: dict, now: int | None = None) -> bool:
    """True bila entri sudah lewat masa berlaku (dan bukan entri abadi)."""
    exp = int(entry.get("expires_ts") or 0)
    if not exp:
        return False
    return int(now if now is not None else time.time()) >= exp


def fmt_ts(ts) -> str:
    """Format tanggal ringkas (YYYY-MM-DD) atau '' bila tak ada."""
    try:
        v = int(ts or 0)
    except Exception:
        return ""
    if v <= 0:
        return ""
    return time.strftime("%Y-%m-%d", time.localtime(v))


# --- Konfinasi path --------------------------------------------------------

def confine(base: Path, candidate: Path) -> Path | None:
    """Pastikan ``candidate`` berada di dalam ``base``. None bila keluar/ilegal."""
    try:
        b = Path(base).resolve()
        c = Path(candidate).resolve()
        c.relative_to(b)
        return c
    except Exception:
        return None


def render_line(entry: dict) -> str:
    """Satu baris cermin untuk sebuah entri: ``- [F] teks · src:user · 2026-10-10``."""
    tag = KIND_TAG.get(normalize_kind(entry.get("kind")), "F")
    text = " ".join(str(entry.get("text") or "").split())
    meta = []
    src = str(entry.get("source") or "unknown")
    if src and src != "unknown":
        meta.append(f"src:{src}")
    d = fmt_ts(entry.get("updated_ts") or entry.get("created_ts"))
    if d:
        meta.append(d)
    suffix = (" · " + " · ".join(meta)) if meta else ""
    return f"- [{tag}] {text}{suffix}"


def render_mirror(entries: list, title: str = "MEMORY") -> str:
    """Rakit cermin Markdown dari entri AKTIF, dikelompokkan per jenis."""
    by_kind: dict = {k: [] for k in KINDS}
    for e in entries or []:
        if str(e.get("status") or STATUS_ACTIVE) != STATUS_ACTIVE:
            continue
        by_kind.setdefault(normalize_kind(e.get("kind")), []).append(e)
    lines = [f"# {title}", "",
             "> Memori jangka panjang lintas sesi. Tag: [F] fakta, [P] preferensi, "
             "[D] keputusan sementara, [G] dugaan.", ""]
    for k in KINDS:
        lines.append(f"## {KIND_SECTION[k]}")
        lines.append("")
        for e in by_kind.get(k, []):
            lines.append(render_line(e))
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"
