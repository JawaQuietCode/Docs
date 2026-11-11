"""downloader.py — tool `download`: ambil media dari URL (TikTok, IG, X, YouTube, dll).

Kenapa tool ini ada: saat owner mengirim tautan dan minta videonya, jalur
"satu perintah shell" terlalu rapuh — agent harus menebak nama berkas, format,
dan apakah yt-dlp terpasang. Akibatnya percakapan berhenti di "tidak bisa" —
padahal alatnya ada. Tool ini menutup jalur itu:

  · memakai modul `yt_dlp` lewat interpreter yang sama (bukan binary di PATH),
  · memasang `yt-dlp` sekali bila belum ada, lalu mencoba lagi,
  · menulis ke data/media/downloads dengan nama yang aman,
  · mengembalikan path berkas + metadata, siap dikirim lewat tool `wa`.

Ini alat operasional, bukan penasihat: kalau gagal, yang dikembalikan adalah
error teknis (baris terakhir stderr), bukan ceramah.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

from security.audit import audit
from tools.builtin.media_naming import rapikan_berkas
from tools.registry import register

SCHEMA = {
    "description": (
        "Unduh media dari URL (TikTok, Instagram, X/Twitter, YouTube, Facebook, "
        "SoundCloud, dan situs yang didukung yt-dlp). Mengembalikan path berkas "
        "lokal + metadata, siap dikirim dengan tool `wa` (action=send_media). "
        "Pakai ini setiap kali owner minta video/audio dari sebuah tautan."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "tautan media (http/https)"},
            "kind": {
                "type": "string",
                "description": "video (bawaan) | audio (ekstrak mp3) | auto",
            },
            "quality": {
                "type": "string",
                "description": "tinggi maksimum video: 1080 (bawaan) atau 720/480. Kalau 1080 tidak ada, otomatis turun ke yang terbaik di bawahnya.",
            },
            "playlist": {
                "type": "boolean",
                "description": "true = unduh seluruh playlist/serial (bawaan: hanya satu item)",
            },
            "cookies": {
                "type": "string",
                "description": "path berkas cookies Netscape (opsional, untuk konten terkunci)",
            },
        },
        "required": ["url"],
    },
}

URL_RE = re.compile(r"^https?://", re.I)


def _root(config) -> Path:
    return Path((config or {}).get("root") or os.getcwd())


def _has_module() -> bool:
    try:
        import yt_dlp  # noqa: F401
        return True
    except Exception:  # noqa: BLE001
        return False


def _install_module() -> tuple[bool, str]:
    """Pasang yt-dlp sekali. Kegagalan dikembalikan apa adanya, bukan disembunyikan."""
    for extra in ([], ["--break-system-packages"]):
        try:
            proc = subprocess.run(
                [sys.executable, "-m", "pip", "install", "--user", "--upgrade", *extra, "yt-dlp"],
                capture_output=True, text=True, timeout=300,
            )
        except Exception as exc:  # noqa: BLE001
            return False, f"pip gagal dijalankan: {exc}"
        if proc.returncode == 0 and _has_module():
            return True, ""
        terakhir = (proc.stderr or proc.stdout or "").strip().splitlines()[-3:]
        pesan = " | ".join(terakhir)
    return False, pesan or "pip selesai tanpa memasang yt-dlp"


def _impersonate() -> list[str]:
    """--impersonate hanya bila curl_cffi ada; tanpa itu yt-dlp menolak argumennya."""
    try:
        import curl_cffi  # noqa: F401
        return ["--impersonate", "chrome"]
    except Exception:  # noqa: BLE001
        return []


def _safe(nama: str, batas: int = 80) -> str:
    bersih = re.sub(r'[\\/:*?"<>|\x00-\x1f]', "_", nama or "media").strip()
    return (bersih[:batas] or "media")


def handler(args, config=None):
    args = args or {}
    url = str(args.get("url") or "").strip()
    if not URL_RE.match(url):
        return {"error": "url harus dimulai http:// atau https://"}
    kind = str(args.get("kind") or "video").strip().lower()
    quality = str(args.get("quality") or "1080").strip()
    if quality.lower() in ("", "best", "auto", "terbaik"):
        quality = "1080"
    playlist = bool(args.get("playlist"))

    if not _has_module():
        ok, err = _install_module()
        if not ok:
            return {"error": f"yt-dlp belum ada dan gagal dipasang: {err}"}

    tujuan = _root(config) / "data" / "media" / "downloads"
    tujuan.mkdir(parents=True, exist_ok=True)
    templat = str(tujuan / "%(id).60s.%(ext)s")

    cmd = [sys.executable, "-m", "yt_dlp",
           "--no-warnings", "--no-progress",
           "--newline", "--no-simulate",
           "--print", "before_dl:%(.{id,title,uploader,upload_date,height})j",
           "--print", "after_move:filepath",
           "-o", templat]
    # Playlist/serial: hanya bila diminta. Anime sering berupa playlist episode.
    cmd.append("--yes-playlist" if playlist else "--no-playlist")
    # Kualitas: 1080 dulu, otomatis turun bila sumbernya hanya 720/480.
    # `-S res:1080` memilih resolusi terdekat di bawah target, jadi tidak perlu
    # rantai format manual yang panjang dan mudah salah.
    target = quality if quality.isdigit() else "1080"
    cmd += ["-f", "bv*+ba/b", "-S", f"res:{target},ext:mp4:m4a"]
    if kind == "audio":
        cmd += ["-x", "--audio-format", "mp3", "--audio-quality", "0"]
    elif kind == "video" and shutil.which("ffmpeg"):
        cmd += ["--merge-output-format", "mp4"]
    cookies = str(args.get("cookies") or "").strip()
    if cookies and Path(cookies).is_file():
        cmd += ["--cookies", cookies]
    cmd += _impersonate()
    cmd.append(url)

    batas = int(((config or {}).get("security") or {}).get("execTimeoutSeconds") or 600)
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=max(120, min(batas, 1800)))
    except subprocess.TimeoutExpired:
        return {"error": f"unduhan melewati batas waktu {batas} detik"}

    keluaran = (proc.stdout or "").strip().splitlines()
    path = ""
    for baris in reversed(keluaran):
        if baris and Path(baris).exists():
            path = baris
            break
    if proc.returncode != 0 or not path:
        ekor = (proc.stderr or proc.stdout or "").strip().splitlines()[-6:]
        audit("download.gagal", url=url, code=proc.returncode)
        return {
            "error": "yt-dlp gagal mengunduh",
            "exit_code": proc.returncode,
            "detail": " | ".join(x for x in ekor if x)[:800],
        }

    meta = {}
    for baris in keluaran:
        if baris.startswith("{"):
            try:
                meta = json.loads(baris)
            except ValueError:
                continue
            break
    berkas = rapikan_berkas(
        Path(path),
        judul=str(meta.get("title") or ""),
        kanal=str(meta.get("uploader") or ""),
        tanggal=str(meta.get("upload_date") or ""),
        kualitas=str(meta.get("height") or ""),
    )
    hasil = {
        "ok": True,
        "path": str(berkas),
        "filename": berkas.name,
        "bytes": berkas.stat().st_size,
        "kind": kind,
    }
    # Judul/durasi dari keluaran yt-dlp bila ada (tanpa memanggil ulang jaringan).
    for baris in keluaran:
        if baris.startswith("[download] Destination:"):
            hasil.setdefault("note", _safe(Path(baris.split(":", 1)[1].strip()).stem))
            break
    audit("download.ok", url=url, bytes=hasil["bytes"], path=str(berkas))
    return hasil


register("download", SCHEMA, handler, toolset="media")
