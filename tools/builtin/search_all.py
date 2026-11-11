"""search_all.py — pencarian lintas sumber (fitur "otak kedua").

Satu kata kunci dicari ke empat tempat sekaligus:

  1. **riwayat chat**  — SQLite `data/memory/kwee.db` (pesan lama per ruangan)
  2. **berkas kerja**  — isi workspace (teks saja; biner dilewati)
  3. **memori**        — `memory/MEMORY.md` + profil yang disimpan agent
  4. **katalog skill** — nama/deskripsi/trigger skill

Kenapa tidak pakai embeddings: 634 skill + ribuan pesan masih kecil, dan
pencarian kata (dengan pembobotan sederhana ala BM25) jalan tanpa jaringan,
tanpa model tambahan, dan hasilnya bisa dijelaskan ("cocok karena kata X").
"""
from __future__ import annotations

import json
import math
import os
import re
from pathlib import Path

from tools.registry import register

STOP = {
    "yang", "dan", "untuk", "dengan", "dari", "buat", "bikin", "tolong", "saya",
    "aku", "ini", "itu", "ada", "bisa", "mau", "ingin", "sedang", "sudah",
    "belum", "juga", "saja", "gimana", "bagaimana", "kalau", "supaya", "biar",
    "apa", "apakah", "dimana", "mana", "kapan", "siapa", "kenapa", "mengapa",
    "the", "and", "for", "with", "from", "this", "that", "please", "help",
    "kamu", "kau", "nya", "sih", "dong", "ya", "yah", "deh", "kan",
}

BINER = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".mp4", ".mp3", ".ogg", ".opus",
         ".pdf", ".zip", ".gz", ".tar", ".tgz", ".db", ".sqlite", ".woff", ".woff2",
         ".ttf", ".ico", ".webm", ".m4a", ".wav", ".bin", ".pyc"}

MAKS_BERKAS = 2 * 1024 * 1024      # lewati berkas > 2 MB
MAKS_BERKAS_PINDAI = 4000          # batas jumlah berkas yang dipindai


def _tokens(text: str) -> list:
    kata = re.findall(r"[0-9A-Za-z_]+", str(text or "").lower())
    return [k for k in kata if len(k) >= 3 and k not in STOP]


def _skor(kata_kunci: list, teks: str) -> float:
    """Skor sederhana: jumlah kata kunci unik yang muncul, dengan bobot jarang."""
    if not kata_kunci:
        return 0.0
    isi = str(teks or "").lower()
    skor = 0.0
    for k in kata_kunci:
        n = isi.count(k)
        if n:
            skor += 1.0 + math.log(n)
    return skor


def _cuplikan(teks: str, kata_kunci: list, lebar: int = 220) -> str:
    """Ambil potongan di sekitar kemunculan pertama kata kunci."""
    isi = re.sub(r"\s+", " ", str(teks or "")).strip()
    if not isi:
        return ""
    pos = -1
    for k in kata_kunci:
        pos = isi.lower().find(k)
        if pos >= 0:
            break
    if pos < 0:
        return isi[:lebar]
    mulai = max(0, pos - lebar // 3)
    return ("…" if mulai else "") + isi[mulai:mulai + lebar].strip()


# --------------------------------------------------------------------------- #
# Sumber 1: riwayat chat
# --------------------------------------------------------------------------- #
def _cari_riwayat(chat_id: str, kata_kunci: list, limit: int) -> list:
    hasil = []
    if not chat_id:
        return hasil
    try:
        from memory.store import Store
        st = Store("data/memory/kwee.db")
        pesan = st.history(chat_id, limit=400)
    except Exception:
        return hasil
    for m in pesan:
        isi = m.get("content")
        if isinstance(isi, list):          # pesan multimodal
            isi = " ".join(str(x.get("text", "")) for x in isi if isinstance(x, dict))
        isi = str(isi or "")
        if not isi.strip():
            continue
        s = _skor(kata_kunci, isi)
        if s > 0:
            hasil.append({
                "sumber": "riwayat",
                "judul": f"{m.get('role', 'pesan')} · {len(isi)} char",
                "cuplikan": _cuplikan(isi, kata_kunci),
                "skor": round(s, 2),
            })
    return hasil[:limit]


# --------------------------------------------------------------------------- #
# Sumber 2: berkas kerja
# --------------------------------------------------------------------------- #
def _cari_berkas(root: Path, kata_kunci: list, limit: int) -> list:
    hasil = []
    dipindai = 0
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames
                       if d not in {"node_modules", "__pycache__", ".git", ".tmp",
                                    ".pyuser", ".venv", "data"}]
        for nama in filenames:
            if dipindai >= MAKS_BERKAS_PINDAI:
                return hasil
            p = Path(dirpath) / nama
            if p.suffix.lower() in BINER:
                continue
            try:
                if p.stat().st_size > MAKS_BERKAS:
                    continue
            except OSError:
                continue
            dipindai += 1
            try:
                isi = p.read_text("utf-8", "replace")
            except Exception:
                continue
            s = _skor(kata_kunci, isi) + _skor(kata_kunci, str(p))
            if s > 0:
                hasil.append({
                    "sumber": "berkas",
                    "judul": str(p.relative_to(root)),
                    "cuplikan": _cuplikan(isi, kata_kunci),
                    "skor": round(s, 2),
                })
    return hasil[:limit]


# --------------------------------------------------------------------------- #
# Sumber 3: memori
# --------------------------------------------------------------------------- #
def _cari_memori(kata_kunci: list, limit: int) -> list:
    hasil = []
    for jalur in (Path("memory/MEMORY.md"), Path("MEMORY.md")):
        try:
            isi = jalur.read_text("utf-8", "replace")
        except Exception:
            continue
        for baris in isi.splitlines():
            if not baris.strip():
                continue
            s = _skor(kata_kunci, baris)
            if s > 0:
                hasil.append({
                    "sumber": "memori",
                    "judul": jalur.name,
                    "cuplikan": _cuplikan(baris, kata_kunci),
                    "skor": round(s, 2),
                })
    return hasil[:limit]


# --------------------------------------------------------------------------- #
# Sumber 4: katalog skill
# --------------------------------------------------------------------------- #
def _cari_skill(kata_kunci: list, limit: int) -> list:
    hasil = []
    try:
        from skills import loader
        for s in loader.load_external():
            teks = " ".join([s.get("name", ""), s.get("description", ""),
                             " ".join(s.get("triggers") or [])])
            sk = _skor(kata_kunci, teks)
            if sk > 0:
                hasil.append({
                    "sumber": "kemampuan",
                    "judul": s.get("name", "?"),
                    "cuplikan": (s.get("description") or "")[:220],
                    "skor": round(sk, 2),
                })
        for s in loader.load_internal():
            teks = " ".join([s.get("name", ""), s.get("description", "")])
            sk = _skor(kata_kunci, teks)
            if sk > 0:
                hasil.append({
                    "sumber": "kemampuan",
                    "judul": s.get("name", "?"),
                    "cuplikan": (s.get("description") or "")[:220],
                    "skor": round(sk, 2),
                })
    except Exception:
        pass
    return hasil[:limit]


# --------------------------------------------------------------------------- #
# API
# --------------------------------------------------------------------------- #
def search(query: str, chat_id: str = "", limit: int = 6, root: str = ".") -> dict:
    """Cari `query` di semua sumber. Hasil diurutkan berdasarkan skor."""
    q = str(query or "").strip()
    kata_kunci = _tokens(q)
    if not kata_kunci:
        return {"query": q, "total": 0, "hasil": [],
                "catatan": "kata kunci terlalu pendek/umum"}

    root_path = Path(root or ".").resolve()
    semua = []
    semua += _cari_riwayat(chat_id, kata_kunci, limit * 2)
    semua += _cari_memori(kata_kunci, limit)
    semua += _cari_skill(kata_kunci, limit)
    semua += _cari_berkas(root_path, kata_kunci, limit * 2)

    semua.sort(key=lambda h: -h["skor"])
    # Dedupe berdasarkan (sumber, judul) — satu berkas cukup sekali.
    unik, terlihat = [], set()
    for h in semua:
        kunci = (h["sumber"], h["judul"])
        if kunci in terlihat:
            continue
        terlihat.add(kunci)
        unik.append(h)

    return {"query": q, "total": len(unik), "hasil": unik[:limit],
            "sumber_dipindai": sorted({h["sumber"] for h in unik})}


def ringkas(hasil: dict) -> str:
    """Versi teks (dipakai model/CLI): satu baris per temuan."""
    if not hasil.get("hasil"):
        return f"Tidak ada hasil untuk '{hasil.get('query', '')}'."
    baris = [f"Hasil untuk '{hasil['query']}' ({hasil['total']} temuan):"]
    for i, h in enumerate(hasil["hasil"], 1):
        baris.append(f"{i}. [{h['sumber']}] {h['judul']} — {h['cuplikan']}")
    return "\n".join(baris)


SCHEMA = {
    "description": (
        "Cari satu kata kunci di EMPAT sumber sekaligus: riwayat chat, berkas "
        "kerja, memori, dan katalog skill. Pakai ini sebelum bilang tidak tahu "
        "atau sebelum meminta ulang data yang mungkin sudah pernah dibahas."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Kata kunci pencarian"},
            "limit": {"type": "integer", "description": "Jumlah hasil maksimum (default 6)"},
            "chat_id": {"type": "string", "description": "Ruangan yang riwayatnya ikut dicari (opsional)"},
        },
        "required": ["query"],
    },
}


def handler(args, config=None):
    args = args or {}
    cfg = config or {}
    ctx = cfg.get("context") or {}
    query = str(args.get("query") or "").strip()
    if not query:
        return {"error": "query kosong"}
    hasil = search(
        query,
        chat_id=str(args.get("chat_id") or ctx.get("chat_id") or ""),
        limit=max(1, min(int(args.get("limit") or 6), 20)),
        root=str(cfg.get("root") or "."),
    )
    hasil["ringkas"] = ringkas(hasil)
    return hasil


register("search_all", SCHEMA, handler, toolset="core", dangerous=False)
