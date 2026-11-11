"""browser.py — tool `browser`: kendalikan browser sungguhan (Playwright/Chromium).

Ini memberi agent "tangan" di web: buka halaman, ambil teks/HTML, klik, isi form,
jalankan JS, dan **screenshot** (yang otomatis disimpan ke penyimpanan media +
bisa dibaca tool `vision`). Sesi (cookie/login) bertahan antar langkah lewat
profil persisten PER TENANT.

Eksekusi ada di `transport/browser.mjs` (Node) karena Chromium Playwright
terpasang di sisi Node. Tool ini hanya menjembatani: menyiapkan argumen, memanggil
proses Node, lalu merapikan hasil (mis. menyimpan screenshot ke media store).
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

from tools.registry import register


def _to_mp4(src: Path) -> Path | None:
    """Transcode video rekaman (WebM/VP8) ke MP4/H.264 agar bisa diputar WhatsApp.

    Kembalikan path MP4, atau None bila ffmpeg tak ada / gagal (pemanggil
    memakai WebM apa adanya). ffmpeg dicari di PATH lalu ~/.local/bin.
    """
    ff = shutil.which("ffmpeg") or str(Path.home() / ".local" / "bin" / "ffmpeg")
    if not Path(ff).exists() and not shutil.which("ffmpeg"):
        return None
    dst = src.with_suffix(".mp4")
    try:
        proc = subprocess.run(
            [ff, "-y", "-loglevel", "error", "-i", str(src),
             "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p",
             "-movflags", "+faststart", "-an", str(dst)],
            capture_output=True, timeout=180,
        )
    except Exception:
        return None
    if proc.returncode == 0 and dst.exists() and dst.stat().st_size > 0:
        return dst
    return None


SCHEMA = {
    "description": (
        "Kendalikan browser sungguhan (Chromium): buka URL, ambil teks/HTML, klik, "
        "isi form, tekan tombol, jalankan JS, ambil tautan/atribut, SCREENSHOT, "
        "UNDUH berkas, kelola tab, atau REKAM video layar. Sesi login bertahan antar "
        "langkah. Screenshot tersimpan sebagai aset media (id `m_xxxx`) dan bisa "
        "dianalisis dengan tool `vision`. Aksi: goto | screenshot | text | html | "
        "attribute | click | dblclick | hover | fill | type | clear | press | "
        "keydown | keyup | select | check | uncheck | scroll | scrollTo | eval | "
        "links | elements | pdf | wait | back | forward | reload | cookies | "
        "storage | upload | download | a11y | metrics | state | tabs | newtab | "
        "closetab | switchtab | headers | viewport | geolocation | permissions | "
        "trace | record."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "action": {"type": "string", "description": "aksi (default: goto)"},
            "url": {"type": "string", "description": "URL untuk goto/screenshot/record"},
            "selector": {"type": "string", "description": "CSS selector untuk click/fill/type/text/html/wait"},
            "value": {"type": "string", "description": "teks untuk fill/type/select"},
            "script": {"type": "string", "description": "JS untuk aksi eval"},
            "key": {"type": "string", "description": "tombol untuk aksi press (mis. Enter)"},
            "full": {"type": "boolean", "description": "screenshot seluruh halaman"},
            "headed": {"type": "boolean", "description": "tampilkan browser (default headless)"},
            "duration": {"type": "integer", "description": "durasi rekam (detik) untuk aksi record, 1-600"},
            "waitUntil": {"type": "string", "description": "load|domcontentloaded|networkidle"},
            "settleMs": {"type": "integer", "description": "jeda tambahan setelah aksi (ms)"},
            "timeout": {"type": "integer", "description": "batas waktu (ms)"},
            "attribute": {"type": "string", "description": "nama atribut untuk aksi attribute (mis. href)"},
            "index": {"type": "integer", "description": "indeks elemen untuk click/elements"},
            "limit": {"type": "integer", "description": "batas jumlah untuk text/links/elements"},
            "x": {"type": "integer", "description": "koordinat X untuk klik/scroll"},
            "y": {"type": "integer", "description": "koordinat Y untuk klik/scroll"},
            "deltaY": {"type": "integer", "description": "jarak gulir vertikal"},
            "tab": {"type": "integer", "description": "indeks tab untuk switchtab"},
            "path": {"type": "string", "description": "path berkas untuk upload"},
            "quality": {"type": "integer", "description": "kualitas JPEG 0-100"},
        },
        "required": ["action"],
    },
}

# Semua aksi yang benar-benar ada di transport/browser.mjs. Dulu daftar ini
# hanya 16 aksi, jadi 25 aksi lain (download, upload, tabs, scroll, attribute,
# elements, a11y, metrics, state, trace, dll) DITOLAK walau mesinnya mendukung —
# itulah "browser belum terintegrasi".
_ALLOWED = {
    "goto", "screenshot", "text", "html", "attribute", "click", "dblclick",
    "hover", "fill", "type", "clear", "press", "keydown", "keyup", "select",
    "check", "uncheck", "scroll", "scrollTo", "eval", "links", "elements",
    "pdf", "wait", "back", "forward", "reload", "cookies", "storage", "upload",
    "download", "a11y", "metrics", "state", "tabs", "newtab", "closetab",
    "switchtab", "headers", "viewport", "geolocation", "permissions", "trace",
    "record",
}

# Argumen skalar yang diteruskan apa adanya ke browser.mjs.
_SCALAR_ARGS = (
    "attribute", "index", "limit", "x", "y", "deltaY", "amount", "tab",
    "name", "path", "quality", "type", "waitUntil", "settleMs", "timeout",
    "locale", "timezone", "userAgent", "proxy", "clip", "value",
)

# Argumen teks panjang dikirim lewat berkas agar aman dari batas panjang argv.
_TEXT_ARGS = ("value", "script")


def _node_bin() -> str:
    return os.environ.get("NODE_BIN", "node")


def _script_path() -> str:
    root = (os.environ.get("KWEE_ROOT") or os.getcwd())
    return str(Path(root) / "transport" / "browser.mjs")


def _profile_dir() -> str:
    """Folder profil browser PER TENANT (cookie/login tidak bercampur)."""
    base = None
    try:
        from security.tenant_context import current_media_dir
        base = current_media_dir()
    except Exception:
        base = None
    root = Path(base) if base else Path("data/media")
    d = root.parent / "browser" if base else Path("data/browser")
    try:
        d.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    return str(d)


def _shot_dir() -> str:
    try:
        from media import store as media_store
        d = media_store.media_root() / "files" / "browser"
    except Exception:
        d = Path("data/media/files/browser")
    try:
        d.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    return str(d)


def handler(args, config=None):
    args = args or {}
    action = str(args.get("action") or "goto").strip().lower()
    if action not in _ALLOWED:
        return {"error": f"aksi tidak dikenal: {action}. Pilihan: {', '.join(sorted(_ALLOWED))}"}

    node = shutil.which(_node_bin())
    if not node:
        return {"error": "Node.js tidak ditemukan di PATH"}
    script = _script_path()
    if not Path(script).exists():
        return {"error": f"skrip browser tidak ditemukan: {script}"}

    sec = (config or {}).get("security", {})
    default_timeout = int(sec.get("execTimeoutSeconds", 300)) * 1000
    timeout_ms = int(args.get("timeout") or 30000)

    duration = args.get("duration")
    try:
        duration = max(1, min(600, int(duration))) if duration is not None else 10
    except (TypeError, ValueError):
        duration = 10

    cmd = [node, script, f"--action={action}",
           f"--profile={_profile_dir()}", f"--shotDir={_shot_dir()}",
           f"--timeout={timeout_ms}"]
    # Rekaman butuh waktu eksekusi ≥ durasi + margin untuk launch/close.
    if action == "record":
        cmd.append(f"--duration={duration}")
        default_timeout = max(default_timeout, (duration + 30) * 1000)
    if args.get("url"):
        cmd.append(f"--url={args['url']}")
    if args.get("selector"):
        cmd.append(f"--selector={args['selector']}")
    if args.get("key"):
        cmd.append(f"--key={args['key']}")
    if args.get("waitUntil"):
        cmd.append(f"--waitUntil={args['waitUntil']}")
    if args.get("settleMs"):
        cmd.append(f"--settleMs={args['settleMs']}")
    if args.get("full"):
        cmd.append("--full")
    if args.get("headed"):
        cmd.append("--headed")
    for nama in _SCALAR_ARGS:
        nilai = args.get(nama)
        if nilai in (None, "", False):
            continue
        if nama in ("value", "type", "waitUntil", "timeout", "settleMs", "quality"):
            # `type` di sini berarti jenis gambar; jangan bentrok dengan aksi.
            if nama == "type" and action != "screenshot":
                continue
        cmd.append(f"--{nama}={nilai}")

    tmp_files = []
    for name in _TEXT_ARGS:
        if args.get(name):
            try:
                tf = Path(_shot_dir()) / f"arg-{name}-{os.getpid()}.txt"
                tf.write_text(str(args[name]), encoding="utf-8")
                tmp_files.append(tf)
                cmd.append(f"--{name}File={tf}")
            except Exception:
                cmd.append(f"--{name}={str(args[name])[:2000]}")

    try:
        proc = subprocess.run(cmd, capture_output=True, text=True,
                              timeout=default_timeout, cwd=os.environ.get("KWEE_ROOT") or os.getcwd())
    except subprocess.TimeoutExpired:
        return {"error": f"browser melebihi batas {default_timeout // 1000} detik"}
    except Exception as exc:  # noqa: BLE001
        return {"error": f"gagal menjalankan browser: {exc}"}
    finally:
        for tf in tmp_files:
            try:
                tf.unlink()
            except Exception:
                pass

    # Ambil baris JSON terakhir dari stdout (log lain ke stderr).
    line = ""
    for ln in (proc.stdout or "").splitlines():
        ln = ln.strip()
        if ln.startswith("{") and ln.endswith("}"):
            line = ln
    if not line:
        err = (proc.stderr or proc.stdout or "").strip()[-400:]
        return {"error": f"browser tidak memberi hasil. {err}"}
    try:
        out = json.loads(line)
    except Exception:
        return {"error": "hasil browser tidak bisa dibaca"}

    if not out.get("ok"):
        return {"error": out.get("error") or "aksi browser gagal"}

    # Screenshot/PDF/Rekaman → simpan sebagai ASET MEDIA supaya bisa dirujuk.
    p = out.get("path")
    if p and Path(p).exists() and action in ("screenshot", "pdf", "record"):
        try:
            from media import store as media_store
            src, mime = Path(p), ""
            if action == "screenshot":
                kind = "image"
            elif action == "pdf":
                kind = "document"
            else:
                kind = "video"
                # WhatsApp memutar MP4/H.264, bukan WebM — transcode bila bisa.
                mp4 = _to_mp4(src)
                if mp4:
                    src = mp4
                mime = "video/mp4" if src.suffix.lower() == ".mp4" else "video/webm"
            note = f"{action} {out.get('url', '')}".strip()
            if action == "record":
                note = f"rekaman {out.get('durationSec', '')}s {out.get('url', '')}".strip()
            entry = media_store.save_bytes(src.read_bytes(), kind=kind, mime=mime,
                                           filename=src.name, source="browser", note=note)
            if entry and not entry.get("error"):
                out["asset"] = entry["id"]
                out["media_path"] = entry["path"]
                out["kind"] = kind
        except Exception:
            pass
        # Hapus berkas sementara mentah bila sudah disalin.
        try:
            if out.get("media_path") and out["media_path"] != p:
                Path(p).unlink()
        except Exception:
            pass
        # Bersihkan folder video sementara Playwright.
        if action == "record":
            try:
                for child in Path(p).parent.iterdir():
                    child.unlink()
                Path(p).parent.rmdir()
            except Exception:
                pass

    return out


register("browser", SCHEMA, handler, toolset="core", dangerous=False)
