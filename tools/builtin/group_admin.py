"""group_admin.py — tool `group_admin`: admin grup berbasis dokumen.

Tiga tugas, semuanya tanpa AI tambahan (murah & bisa diuji):

1. **FAQ dari dokumen** — owner menaruh berkas (`.md`, `.txt`, `.json`, `.csv`)
   di `data/knowledge/<ruangan>/`. Perintah `index` memecahnya jadi potongan,
   `ask` mencari potongan paling relevan beserta rujukan (berkas + nomor baris).
2. **Sambutan** — teks sambutan anggota baru, bisa diubah owner
   (`data/knowledge/<ruangan>/sapaan.md`).
3. **Deteksi spam** — heuristik ringan (tautan berulang, teks berkapital
   berlebihan, kata penipuan, mention massal). Hasilnya `{spam, alasan}`;
   keputusan menghapus tetap di tangan owner/transport.

Kenapa tidak pakai LLM untuk pencarian: FAQ grup harus bisa dijawab dalam
<1 detik, gratis, dan konsisten. Model tetap dipakai kalau owner ingin jawaban
yang dirangkai ulang — tool ini mengembalikan potongan + sumbernya.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from tools.registry import register

TEKS = {".md", ".txt", ".json", ".csv", ".yaml", ".yml"}
MAKS_ISI = 400_000          # berkas lebih besar dari ini dilewati
POTONGAN = 700              # panjang satu potongan (karakter)

SPAM_KATA = (
    "klik link", "klik tautan", "bonus deposit", "slot gacor", "pinjaman cepat",
    "investasi pasti", "profit pasti", "gratis pulsa", "menang undian",
    "cek resi", "akun anda diblokir", "verifikasi akun", "kirim kode otp",
    "transfer sekarang", "hadiah menganggur", "lowongan part time",
)


def _dir(chat_id: str) -> Path:
    aman = re.sub(r"[^0-9A-Za-z._@-]", "_", str(chat_id or "umum"))[:80]
    return Path("data") / "knowledge" / aman


def _berkas(chat_id: str) -> list:
    d = _dir(chat_id)
    if not d.is_dir():
        return []
    return sorted(p for p in d.iterdir() if p.is_file() and p.suffix.lower() in TEKS)


def _potong(teks: str) -> list:
    """Pecah jadi potongan ~POTONGAN karakter, jaga batas baris."""
    baris = teks.splitlines()
    out, buf, panjang = [], [], 0
    for b in baris:
        buf.append(b)
        panjang += len(b) + 1
        if panjang >= POTONGAN:
            out.append("\n".join(buf).strip())
            buf, panjang = [], 0
    if buf:
        out.append("\n".join(buf).strip())
    return [p for p in out if p]


def index(chat_id: str) -> dict:
    """Bangun indeks potongan dari semua dokumen ruangan ini."""
    d = _dir(chat_id)
    if not d.is_dir():
        return {"ok": False, "error": f"belum ada folder dokumen: {d}",
                "petunjuk": "taruh berkas .md/.txt di folder itu (bisa lewat tool files)"}
    docs = []
    total = 0
    for p in _berkas(chat_id):
        try:
            if p.stat().st_size > MAKS_ISI:
                continue
            isi = p.read_text("utf-8", "replace")
        except Exception:
            continue
        potongan = _potong(isi)
        total += len(potongan)
        docs.append({
            "berkas": p.name,
            "chars": len(isi),
            "sha256": hashlib.sha256(isi.encode("utf-8", "replace")).hexdigest()[:16],
            "potongan": potongan,
        })
    indeks = {"chat_id": chat_id, "docs": docs, "jumlah_potongan": total}
    try:
        (d / "index.json").write_text(json.dumps(indeks, ensure_ascii=False), "utf-8")
    except Exception:
        pass
    return {"ok": True, "dokumen": len(docs), "potongan": total,
            "berkas": [x["berkas"] for x in docs]}


def _muat_index(chat_id: str) -> dict:
    d = _dir(chat_id)
    jalur = d / "index.json"
    if jalur.is_file():
        try:
            data = json.loads(jalur.read_text("utf-8"))
            # Indeks basi (berkas berubah) → bangun ulang.
            if data.get("docs") and all(
                (d / x["berkas"]).is_file() for x in data["docs"]
            ):
                return data
        except Exception:
            pass
    hasil = index(chat_id)
    if not hasil.get("ok"):
        return {"docs": [], "jumlah_potongan": 0}
    try:
        return json.loads((d / "index.json").read_text("utf-8"))
    except Exception:
        return {"docs": [], "jumlah_potongan": 0}


def _kata(text: str) -> list:
    kata = re.findall(r"[0-9A-Za-z_]+", str(text or "").lower())
    return [k for k in kata if len(k) >= 3]


def ask(chat_id: str, pertanyaan: str, limit: int = 3) -> dict:
    """Cari potongan dokumen yang paling relevan untuk pertanyaan."""
    q = str(pertanyaan or "").strip()
    if not q:
        return {"error": "pertanyaan kosong"}
    indeks = _muat_index(chat_id)
    kata = _kata(q)
    if not kata:
        return {"jawaban": "Pertanyaannya terlalu pendek.", "sumber": []}

    skor = []
    for doc in indeks.get("docs") or []:
        for i, pot in enumerate(doc.get("potongan") or []):
            isi = pot.lower()
            hit = sum(1 for k in kata if k in isi)
            if hit:
                # Bobot: jumlah kata kunci berbeda + kerapatan kemunculan.
                skor.append((hit + min(isi.count(kata[0]), 3) * 0.2,
                             doc["berkas"], i, pot))
    skor.sort(key=lambda x: -x[0])
    if not skor:
        return {"jawaban": "Belum ada dokumen yang cocok. Mungkin belum diindeks "
                           "atau topiknya memang tidak ada di dokumen grup.",
                "sumber": [], "dokumen": len(indeks.get("docs") or [])}

    teratas = skor[:max(1, int(limit))]
    jawaban = "\n\n".join(p[3][:900] for p in teratas)
    sumber = [{"judul": p[1], "halaman": f"potongan {p[2] + 1}",
               "cuplikan": p[3][:160]} for p in teratas]
    return {"jawaban": jawaban, "sumber": sumber,
            "dokumen": len(indeks.get("docs") or [])}


def welcome(chat_id: str, nama: str = "", grup: str = "") -> dict:
    """Teks sambutan anggota baru (bisa diubah owner lewat sapaan.md)."""
    d = _dir(chat_id)
    teks = ""
    for nama_berkas in ("sapaan.md", "welcome.md"):
        p = d / nama_berkas
        if p.is_file():
            try:
                teks = p.read_text("utf-8", "replace").strip()
                break
            except Exception:
                continue
    if not teks:
        teks = (
            "Hai {nama} ♡ selamat datang di {grup}.\n"
            "Baca dulu aturan yang dipin ya. Kalau ada yang mau ditanya, "
            "tulis saja di sini — aku bantu jawab dari dokumen grup."
        )
    return {"ok": True,
            "teks": teks.replace("{nama}", nama or "kak").replace("{grup}", grup or "grup ini")}


def rules(chat_id: str) -> dict:
    d = _dir(chat_id)
    for nama in ("aturan.md", "rules.md"):
        p = d / nama
        if p.is_file():
            try:
                return {"ok": True, "teks": p.read_text("utf-8", "replace").strip()}
            except Exception:
                pass
    return {"ok": False, "error": "aturan.md belum ada di folder dokumen grup"}


def spam_check(text: str, pengirim: str = "") -> dict:
    """Heuristik spam ringan — TIDAK menghapus apa pun, hanya menilai."""
    isi = str(text or "")
    alasan = []
    if len(isi) > 1500:
        alasan.append("pesan sangat panjang")
    tautan = re.findall(r"https?://\S+", isi)
    if len(tautan) >= 3:
        alasan.append(f"{len(tautan)} tautan dalam satu pesan")
    huruf = [c for c in isi if c.isalpha()]
    if huruf and sum(1 for c in huruf if c.isupper()) / len(huruf) > 0.6 and len(isi) > 40:
        alasan.append("mayoritas huruf kapital")
    rendah = isi.lower()
    for kata in SPAM_KATA:
        if kata in rendah:
            alasan.append(f"frasa mencurigakan: '{kata}'")
            break
    if len(re.findall(r"@\d{6,}", isi)) >= 5:
        alasan.append("mention massal")
    if re.search(r"(.)\1{9,}", isi):
        alasan.append("karakter berulang berlebihan")
    return {"spam": bool(alasan), "alasan": "; ".join(alasan),
            "pengirim": pengirim, "cuplikan": isi[:200]}


SCHEMA = {
    "description": (
        "Admin grup berbasis dokumen: jawab pertanyaan anggota dari berkas di "
        "data/knowledge/<ruangan>/, siapkan sambutan anggota baru, dan nilai "
        "apakah sebuah pesan terindikasi spam. Pakai `ask` sebelum menjawab "
        "pertanyaan faktual tentang aturan/harga/produk grup."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["ask", "index", "list", "welcome", "rules", "spam_check"],
                "description": "Tindakan yang dijalankan",
            },
            "pertanyaan": {"type": "string", "description": "Pertanyaan anggota (untuk ask)"},
            "teks": {"type": "string", "description": "Isi pesan yang mau dinilai (spam_check)"},
            "pengirim": {"type": "string", "description": "Nama/nomor pengirim (opsional)"},
            "nama": {"type": "string", "description": "Nama anggota baru (welcome)"},
            "grup": {"type": "string", "description": "Nama grup (welcome)"},
            "limit": {"type": "integer", "description": "Jumlah potongan jawaban (default 3)"},
        },
        "required": ["action"],
    },
}


def handler(args, config=None):
    args = args or {}
    cfg = config or {}
    chat_id = str((cfg.get("context") or {}).get("chat_id") or "umum")
    action = str(args.get("action") or "ask").lower()

    if action == "index":
        return index(chat_id)
    if action == "list":
        berkas = _berkas(chat_id)
        return {"ok": True, "folder": str(_dir(chat_id)),
                "dokumen": [p.name for p in berkas],
                "jumlah": len(berkas)}
    if action == "ask":
        return ask(chat_id, args.get("pertanyaan") or args.get("teks") or "",
                   int(args.get("limit") or 3))
    if action == "welcome":
        return welcome(chat_id, str(args.get("nama") or ""), str(args.get("grup") or ""))
    if action == "rules":
        return rules(chat_id)
    if action == "spam_check":
        return spam_check(args.get("teks") or "", str(args.get("pengirim") or ""))
    return {"error": f"action tidak dikenal: {action}"}


register("group_admin", SCHEMA, handler, toolset="core", dangerous=False)
