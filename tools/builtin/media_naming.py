"""Penamaan berkas media: judul rapi, aman lintas OS, tanpa tabrakan."""
from __future__ import annotations

import re
import unicodedata
from pathlib import Path

BATAS_BYTE = 180
BATAS_JUDUL = 120
BATAS_KANAL = 40

_TERLARANG = re.compile(r'[\\/:*?"<>|\x00-\x1f\x7f]')
_TAK_TAMPAK = re.compile("[\u200b-\u200f\u202a-\u202e\u2060-\u206f\ufeff]")
_CADANGAN = {"con", "prn", "aux", "nul", *(f"com{i}" for i in range(1, 10)), *(f"lpt{i}" for i in range(1, 10))}
_TANGGAL = re.compile(r"^(\d{4})(\d{2})(\d{2})$")


def bersihkan(teks, batas: int = BATAS_JUDUL) -> str:
    s = unicodedata.normalize("NFC", str(teks or ""))
    s = _TAK_TAMPAK.sub("", s)
    s = _TERLARANG.sub(" ", s)
    s = re.sub(r"\s+", " ", s).strip(" .")
    while len(s.encode("utf-8")) > batas:
        s = s[:-1]
    s = s.strip(" .-_")
    if s.lower() in _CADANGAN:
        s = f"{s}_"
    return s or "media"


def format_tanggal(nilai) -> str:
    teks = str(nilai or "").strip()
    cocok = _TANGGAL.match(teks)
    if cocok:
        return "-".join(cocok.groups())
    return bersihkan(teks, 20) if teks else ""


def nama_media(judul, *, kanal="", tanggal="", kualitas="", ext="mp4") -> str:
    jud = bersihkan(judul, BATAS_JUDUL)
    kn = bersihkan(kanal, BATAS_KANAL) if kanal else ""
    detail = ", ".join(x for x in (format_tanggal(tanggal), f"{kualitas}p" if str(kualitas or "").isdigit() else "") if x)
    stem = jud
    if kn:
        stem = f"{stem} - {kn}"
    if detail:
        stem = f"{stem} ({detail})"
    stem = bersihkan(stem, BATAS_BYTE - len(ext) - 1)
    ekstensi = re.sub(r"[^a-z0-9]", "", str(ext or "").lower())[:8]
    return f"{stem}.{ekstensi}" if ekstensi else stem


def unik(path: Path) -> Path:
    if not path.exists():
        return path
    induk, stem, ext = path.parent, path.stem, path.suffix
    n = 2
    while True:
        kandidat = induk / f"{stem} ({n}){ext}"
        if not kandidat.exists():
            return kandidat
        n += 1


def rapikan_berkas(path: Path, *, judul="", kanal="", tanggal="", kualitas="") -> Path:
    path = Path(path)
    if not path.exists():
        return path
    nama = nama_media(
        judul or path.stem,
        kanal=kanal,
        tanggal=tanggal,
        kualitas=kualitas,
        ext=path.suffix.lstrip("."),
    )
    if path.name == nama:
        return path
    baru = unik(path.with_name(nama))
    path.rename(baru)
    return baru
