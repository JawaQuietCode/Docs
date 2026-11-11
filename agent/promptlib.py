"""promptlib.py — kelola PROMPT aktif (persona & rules) tanpa restart.

Kenapa modul ini ada
--------------------
Dulu persona HANYA bisa diganti dengan mengedit `prompts/persona.md` lalu
me-restart bot: `agent/context.py` membaca berkas itu pada setiap pesan tetapi
tidak ada cara melihat apa yang sedang aktif, tidak ada cara memilih varian
(`prompts/persona.mommy-girlfriend.md` ada tetapi tidak pernah terpakai), dan
tidak ada cara memuat ulang setelah berkas diubah.

Sekarang:
  * `prompts/*.md` (selain `rules.md`) adalah PERSONA yang bisa dipilih;
    pilihan disimpan di `data/prompt_active.txt` (atomik, tanpa restart).
  * Urutan prioritas: override milik tenant → persona aktif → `persona.md`.
  * `info()` memberi ringkasan untuk perintah `!prompt` / `GET /prompt`:
    persona mana yang dipakai, dari berkas mana, berapa karakter, kapan diubah,
    dan daftar persona lain yang tersedia.
  * Isi berkas di-cache berdasarkan mtime, jadi mengedit persona langsung
    terpakai pada pesan berikutnya tanpa memanggil disk berkali-kali.

Batas keamanan: hanya OWNER yang boleh mengganti persona (diperiksa di
`agent/main.py` dan `transport/commands.js`), dan isi persona TIDAK PERNAH
dikirim ke WhatsApp secara utuh — hanya ringkasan + potongan awal.
"""
from __future__ import annotations

import os
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PROMPTS_DIR = ROOT / "prompts"
STATE_FILE = ROOT / "data" / "prompt_active.txt"
DEFAULT_PERSONA = "persona"
RULES_NAME = "rules"

# Cache isi berkas: {path: (mtime_ns, text)}
_CACHE: dict = {}
_TITLE_RE = re.compile(r"^#\s+(.+)$", re.M)


def clear_cache() -> None:
    """Lupakan cache isi berkas (dipakai `!prompt reload`)."""
    _CACHE.clear()


def _read(path: Path) -> str:
    try:
        mtime = path.stat().st_mtime_ns
    except Exception:
        _CACHE.pop(str(path), None)
        return ""
    hit = _CACHE.get(str(path))
    if hit and hit[0] == mtime:
        return hit[1]
    try:
        text = path.read_text(encoding="utf-8")
    except Exception as exc:  # noqa: BLE001
        print(f"[prompt] gagal membaca {path}: {exc}", flush=True)
        return ""
    _CACHE[str(path)] = (mtime, text)
    return text


def _title(text: str, fallback: str) -> str:
    m = _TITLE_RE.search(text or "")
    return (m.group(1).strip() if m else fallback)[:80]


def available() -> list:
    """Daftar persona yang tersedia di `prompts/` (kecuali rules)."""
    out = []
    if not PROMPTS_DIR.is_dir():
        return out
    for p in sorted(PROMPTS_DIR.glob("*.md")):
        name = p.stem
        if name == RULES_NAME:
            continue
        text = _read(p)
        if not text.strip():
            continue
        try:
            st = p.stat()
            mtime, size = st.st_mtime, st.st_size
        except Exception:
            mtime, size = 0.0, 0
        out.append({
            "name": name,
            "file": p.name,
            "title": _title(text, name),
            "chars": len(text),
            "bytes": size,
            "modified": mtime,
            "aktif": name == active(),
        })
    return out


def active() -> str:
    """Nama persona aktif (dibaca dari `data/prompt_active.txt`)."""
    try:
        raw = STATE_FILE.read_text(encoding="utf-8").strip()
    except Exception:
        return DEFAULT_PERSONA
    name = raw.splitlines()[0].strip() if raw else ""
    if not name or not (PROMPTS_DIR / f"{name}.md").is_file():
        return DEFAULT_PERSONA
    return name


def _write_state(name: str) -> None:
    """Tulis pilihan persona secara atomik (tmp + os.replace)."""
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE_FILE.with_suffix(".tmp")
    tmp.write_text(name + "\n", encoding="utf-8")
    os.replace(tmp, STATE_FILE)


def set_active(name: str) -> dict:
    """Pilih persona lain. `name` harus salah satu dari `available()`."""
    want = str(name or "").strip()
    if not want:
        return {"error": "nama persona kosong"}
    # Terima beberapa penulisan: "persona.mommy-girlfriend" (nama berkas),
    # "mommy-girlfriend" (tanpa prefix), atau "persona.mommy-girlfriend.md".
    # Dulu prefix "persona." selalu DIBUANG, sehingga varian
    # `persona.mommy-girlfriend.md` justru tidak pernah ketemu.
    raw = want[:-3] if want.endswith(".md") else want
    kandidat = [raw]
    if raw.startswith("persona."):
        kandidat.append(raw[len("persona."):])
    else:
        kandidat.append(f"persona.{raw}")
    want = next((c for c in kandidat if (PROMPTS_DIR / f"{c}.md").is_file()), raw)
    target = PROMPTS_DIR / f"{want}.md"
    if want == RULES_NAME or not target.is_file():
        names = [x["name"] for x in available()]
        return {"error": f"persona '{raw}' tidak ada", "tersedia": names}
    if not _read(target).strip():
        return {"error": f"berkas persona '{target.name}' kosong"}
    try:
        _write_state(want)
    except Exception as exc:  # noqa: BLE001
        return {"error": f"gagal menyimpan pilihan persona: {exc}"}
    clear_cache()
    return {"ok": True, "aktif": want, "file": target.name}


def reset() -> dict:
    """Kembali ke persona bawaan (`persona.md`)."""
    try:
        if STATE_FILE.exists():
            STATE_FILE.unlink()
    except Exception as exc:  # noqa: BLE001
        return {"error": f"gagal menghapus pilihan persona: {exc}"}
    return {"ok": True, "aktif": DEFAULT_PERSONA}


def tenant_override() -> dict:
    """Persona khusus tenant aktif (`<ruangan>/persona.md`) bila ada."""
    try:
        from security.tenant_context import get_current
        cur = get_current() or {}
        root = cur.get("root")
        if not root:
            return {}
        p = Path(root) / "persona.md"
        if not p.is_file():
            return {}
        text = _read(p)
        if not text.strip():
            return {}
        return {"file": str(p), "chars": len(text), "tenant": cur.get("tenant") or ""}
    except Exception:
        return {}


def resolve() -> dict:
    """Persona EFEKTIF: tenant override → persona aktif → persona.md."""
    ov = tenant_override()
    if ov:
        text = _read(Path(ov["file"]))
        return {"name": f"tenant:{ov.get('tenant') or '?'}", "file": ov["file"],
                "text": text, "source": "override-tenant"}
    name = active()
    path = PROMPTS_DIR / f"{name}.md"
    text = _read(path)
    if not text.strip() and name != DEFAULT_PERSONA:
        name = DEFAULT_PERSONA
        path = PROMPTS_DIR / f"{name}.md"
        text = _read(path)
    return {"name": name, "file": str(path), "text": text,
            "source": "prompts" if path.is_file() else "tidak-ada"}


def rules_text() -> str:
    return _read(PROMPTS_DIR / f"{RULES_NAME}.md")


def info(preview_chars: int = 240) -> dict:
    """Ringkasan untuk `!prompt` / `GET /prompt`."""
    cur = resolve()
    rules = rules_text()
    text = cur.get("text") or ""
    return {
        "ok": True,
        "aktif": cur.get("name"),
        "file": cur.get("file"),
        "sumber": cur.get("source"),
        "persona_chars": len(text),
        "persona_preview": " ".join(text.split())[:preview_chars],
        "rules_chars": len(rules),
        "rules_file": str(PROMPTS_DIR / f"{RULES_NAME}.md"),
        "judul": _title(text, cur.get("name") or ""),
        "tenant_override": tenant_override() or None,
        "tersedia": available(),
        "state_file": str(STATE_FILE),
    }
