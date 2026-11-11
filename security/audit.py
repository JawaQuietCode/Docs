"""audit.py — catat setiap aksi berbahaya/bermakna ke data/audit.log (JSONL).

Tidak pernah mencatat rahasia: nilai yang tampak seperti key/token disunting.

Multi-tenant: bila ``security.tenant_context`` menetapkan tenant AKTIF, catatan
ditulis ke ``data/tenants/<t>/audit.log`` tenant itu; jika tidak, ke log default
(``AUDIT_LOG`` atau ``data/audit.log``) — perilaku lama tetap utuh.

Privasi memori (Fitur #30): event ``memory.*`` TIDAK PERNAH mencatat isi entri.
Field ``text``/``content``/``value``/``line``/``body`` dibuang, dan nilai apa pun
yang tampak seperti rahasia disunting — jadi laporan audit tak bisa membocorkan
isi memori pengguna.
"""
import json
import os
import re
import time
from pathlib import Path

DEFAULT_LOG = os.environ.get("AUDIT_LOG", "data/audit.log")

_SECRET_KEYS = re.compile(r"(key|token|secret|password|passwd|credential)", re.I)
# Field yang isinya = ISI memori / teks bebas → tak boleh masuk audit.
_CONTENT_KEYS = re.compile(r"^(text|content|value|line|body|entry|memory|note)$", re.I)
# Nilai yang tampak seperti rahasia (pertahanan berlapis, apa pun nama fieldnya).
_SECRET_VALUES = (
    re.compile(r"\bptl[ac]_[A-Za-z0-9]{16,}"),
    re.compile(r"\bsk-[A-Za-z0-9_\-]{16,}"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\beyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{6,}"),
    re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._\-]{20,}"),
    re.compile(r"(?i)\b(?:password|secret|api[_-]?key|token)\s*[:=]\s*\S{4,}"),
)


def _scrub_value(v):
    if isinstance(v, str):
        for rx in _SECRET_VALUES:
            if rx.search(v):
                return "<disunting>"
        return v
    if isinstance(v, dict):
        return _redact(v)
    if isinstance(v, list):
        return [_scrub_value(x) for x in v]
    return v


def _redact(obj):
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            ks = str(k)
            if _CONTENT_KEYS.match(ks):
                out[ks] = "<disunting>"
            elif _SECRET_KEYS.search(ks):
                out[ks] = "<disunting>"
            else:
                out[ks] = _scrub_value(v)
        return out
    if isinstance(obj, list):
        return [_redact(v) for v in obj]
    return _scrub_value(obj)


def _active_path(log_path: str | None) -> Path:
    """Path audit: argumen eksplisit → tenant aktif → default."""
    if log_path:
        return Path(log_path)
    try:
        from security.tenant_context import current_audit_path
        p = current_audit_path()
        if p:
            return Path(p)
    except Exception:
        pass
    return Path(DEFAULT_LOG)


def audit(event: str, log_path: str | None = None, **fields):
    """Tulis satu baris JSON. Aman dipanggil berkali-kali; gagal senyap."""
    path = _active_path(log_path)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        record = {"ts": int(time.time()), "event": event, **_redact(fields)}
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception:
        # Audit tidak boleh menjatuhkan bot.
        pass
