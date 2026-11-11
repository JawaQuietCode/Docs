"""files.py — baca/tulis/ubah/daftar file (dibatasi ke folder proyek).

Aksi:
    read    — baca isi file (mendukung offset/limit baris untuk file besar)
    write   — tulis (timpa) file secara ATOMIK
    append  — tambah di akhir file
    edit    — ganti potongan teks (old_string → new_string) ala coding-agent
    list    — daftar isi folder
    mkdir   — buat folder
    delete  — hapus file/folder (folder hanya bila kosong)

Perbaikan penting dibanding versi lama:
  * `read` punya offset/limit → file 2000 baris bisa dibaca bertahap, tidak
    lagi "buta" atau dipaksa memakai shell.
  * `write`/`edit` menulis ke file sementara lalu `os.replace` → tidak ada
    jendela waktu di mana file kosong/terpotong (dulu `write_text` memotong
    dulu; proses mati di tengah = file rusak).
  * Berkas biner dideteksi (NUL byte) dan dilaporkan jelas, bukan
    UnicodeDecodeError yang muncul sebagai "gagal".
  * Batas output mengikuti `security.outputLimitBytes`, bukan angka mati.
"""
import hashlib
import os
import tempfile
from pathlib import Path

from security.audit import audit
from security.policy import path_allowed
from tools.registry import register
from tools.schema import coerce_bool, coerce_int, output_limit, truncate

SCHEMA = {
    "description": (
        "Kelola file di folder proyek: read, write, append, edit, list, mkdir, delete. "
        "Aksi `edit` mengganti old_string dengan new_string (seperti editor coding-agent). "
        "Untuk file besar, pakai `offset` dan `limit` (baris) pada aksi read."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["read", "write", "append", "edit", "verify", "list", "mkdir", "delete"],
                "description": ("Aksi. `write`/`edit` memverifikasi hasil dengan membaca "
                                "ulang dan membandingkan SHA-256; `verify` memeriksa berkas "
                                "masih utuh (opsional cocokkan `sha256`)."),
            },
            "path": {"type": "string", "description": "Path file/folder (relatif ke root proyek)"},
            "content": {"type": "string", "description": "Isi (untuk write/append)"},
            "sha256": {"type": "string", "description": "Sidik yang diharapkan (untuk verify; boleh 16 karakter awal)"},
            "old_string": {"type": "string", "description": "Teks yang dicari (untuk edit)"},
            "new_string": {"type": "string", "description": "Teks pengganti (untuk edit)"},
            "replace_all": {"type": "boolean", "description": "Ganti semua kemunculan (untuk edit)"},
            "offset": {"type": "integer", "description": "Baris awal (0-based, untuk read)"},
            "limit": {"type": "integer", "description": "Jumlah baris (untuk read, default 800)"},
        },
        "required": ["action", "path"],
    },
}

DEFAULT_READ_LINES = 2000


def _resolve(path: str, config) -> tuple:
    """Kembalikan (target, error). Pastikan target di dalam root yang sah.

    Multi-tenant: bila ada tenant AKTIF yang BUKAN owner utama, root-nya adalah
    workspace tenant itu (terisolasi). Owner utama tetap memakai root proyek
    supaya alur kerja lama tidak berubah.
    """
    sec = (config or {}).get("security", {})
    mode = str(sec.get("accessMode") or "owner").strip().lower()
    deny = sec.get("denyPaths", []) or []
    if deny and not path_allowed(path, deny):
        return None, f"path ditolak oleh kebijakan: {path}"
    root = Path((config or {}).get("root", ".")).resolve()
    try:
        from security.tenant_context import get_current
        cur = get_current() or {}
        if cur and not cur.get("primary") and cur.get("workspace"):
            root = Path(cur["workspace"]).resolve()
    except Exception:
        pass
    mentah = str(path or "").strip()
    # ALIAS: "/agent" sering diketik padahal yang dimaksud "agent" di dalam
    # root proyek. Kalau path absolut itu tidak ada, coba sebagai relatif.
    if mentah.startswith("/") and not Path(mentah).exists():
        kandidat = (root / mentah.lstrip("/"))
        if kandidat.exists():
            mentah = str(kandidat)
    try:
        target = (root / mentah).resolve() if not Path(mentah).is_absolute() else Path(mentah).resolve()
    except Exception:
        return None, "path tidak valid"
    # Mode owner: tidak ada pengurungan folder — pemilik bot ini bekerja sendiri
    # dan sering perlu menyentuh /home/container, /tmp, atau folder panel.
    if mode != "owner" and root not in target.parents and target != root:
        return None, "path di luar folder proyek"
    # denyPaths juga diperiksa pada path FINAL (symlink bisa menunjuk ke sana).
    if deny and not path_allowed(str(target), deny):
        return None, f"path ditolak oleh kebijakan: {target}"
    return target, None


def _atomic_write(target: Path, text: str) -> None:
    """Tulis atomik: file sementara di folder yang sama, lalu os.replace."""
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".kwee-", dir=str(target.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        try:
            os.chmod(tmp, target.stat().st_mode & 0o7777)
        except Exception:
            pass
        os.replace(tmp, target)
    except Exception:
        try:
            os.unlink(tmp)
        except Exception:
            pass
        raise


def _sidik(data: bytes) -> str:
    """SHA-256 isi berkas — dipakai untuk membuktikan tulis/baca utuh."""
    return hashlib.sha256(data).hexdigest()


def _verifikasi_tulis(target: Path, diharapkan: bytes) -> dict:
    """Baca ulang berkas setelah ditulis dan bandingkan byte-per-byte.

    Kenapa: `os.replace` atomik, tetapi disk penuh, kuota, atau filesystem
    aneh (overlay Pterodactyl) bisa membuat berkas terpotong TANPA error.
    Tanpa pemeriksaan ini, agent bisa "berhasil" menulis lalu membaca versi
    lama/terpotong dan menyimpulkan hal yang salah tentang kodenya sendiri.
    """
    try:
        nyata = target.read_bytes()
    except Exception as exc:  # noqa: BLE001
        return {"verified": False, "error": f"gagal membaca ulang: {exc}"}
    sama = nyata == diharapkan
    out = {
        "verified": sama,
        "bytes": len(nyata),
        "sha256": _sidik(nyata)[:16],
    }
    if not sama:
        out["error"] = (f"isi berkas berbeda setelah ditulis "
                        f"({len(nyata)} vs {len(diharapkan)} byte)")
    return out


def handler(args, config=None):
    args = args or {}
    action = args.get("action")
    path = str(args.get("path", ""))
    if not path:
        return {"error": "path kosong"}

    target, err = _resolve(path, config)
    if err:
        audit("files.blocked", action=action, path=path, reason=err)
        return {"error": err}

    limit = output_limit(config)

    try:
        if action == "read":
            data = target.read_bytes()
            if b"\x00" in data[:4096]:
                return {"error": f"{path} tampak berkas biner ({len(data)} byte); "
                                 "gunakan tool lain (mis. shell `file`/`xxd`)"}
            text = data.decode("utf-8", "replace")
            lines = text.splitlines()
            total = len(lines)
            off = coerce_int(args.get("offset"), 0, minimum=0, maximum=max(0, total))
            n = coerce_int(args.get("limit"), DEFAULT_READ_LINES, minimum=1, maximum=20000)
            chunk = "\n".join(lines[off:off + n])
            out = {
                "content": truncate(chunk, limit),
                "path": str(target),
                "total_lines": total,
                "offset": off,
                "returned_lines": min(n, max(0, total - off)),
                # INTEGRITAS: sidik seluruh berkas + penanda bila ada byte yang
                # tidak sah (U+FFFD) karena decoding pengganti. Tanpa ini,
                # agent bisa menganggap berkas "utuh" padahal ada bagian rusak.
                "sha256": _sidik(data)[:16],
                "bytes": len(data),
                "utf8_valid": "\ufffd" not in text,
            }
            if off + n < total:
                out["next_offset"] = off + n
                out["note"] = (f"masih ada {total - (off + n)} baris; "
                               f"panggil lagi dengan offset={off + n}")
            return out

        if action == "write":
            content = str(args.get("content", ""))
            data = content.encode("utf-8")
            _atomic_write(target, content)
            cek = _verifikasi_tulis(target, data)
            audit("files.write", path=path, bytes=len(data), verified=cek.get("verified"))
            if not cek.get("verified"):
                return {"ok": False, "path": str(target), **cek,
                        "error": cek.get("error") or "tulis tidak terverifikasi"}
            return {"ok": True, "path": str(target), "bytes": len(data), **cek}

        if action == "append":
            content = str(args.get("content", ""))
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open("a", encoding="utf-8") as fh:
                fh.write(content)
            audit("files.append", path=path, bytes=len(content.encode("utf-8")))
            return {"ok": True, "path": str(target)}

        if action == "edit":
            old = args.get("old_string", "")
            new = args.get("new_string", "")
            if old == "":
                return {"error": "old_string kosong"}
            raw = target.read_bytes()
            if b"\x00" in raw[:4096]:
                return {"error": f"{path} tampak berkas biner; tidak bisa diedit sebagai teks"}
            text = raw.decode("utf-8", "replace")
            crlf = "\r\n" in text
            count = text.count(old)
            if count == 0:
                return {"error": "old_string tidak ditemukan"}
            if count > 1 and not coerce_bool(args.get("replace_all")):
                return {"error": f"old_string muncul {count}x; set replace_all=true atau perjelas konteks"}
            text = text.replace(old, new) if coerce_bool(args.get("replace_all")) else text.replace(old, new, 1)
            _atomic_write(target, text)
            cek = _verifikasi_tulis(target, text.encode("utf-8"))
            replaced = count if coerce_bool(args.get("replace_all")) else 1
            audit("files.edit", path=path, replaced=replaced, verified=cek.get("verified"))
            if not cek.get("verified"):
                return {"ok": False, "path": str(target), **cek,
                        "error": cek.get("error") or "edit tidak terverifikasi"}
            return {"ok": True, "path": str(target), "replaced": replaced,
                    "crlf_preserved": crlf, **cek}

        if action == "verify":
            # Pastikan berkas masih persis seperti yang diharapkan (mis. setelah
            # agent menulis kode sendiri, atau sebelum deploy).
            data = target.read_bytes()
            nyata = _sidik(data)
            ingin = str(args.get("sha256") or "").strip().lower()
            out = {"ok": True, "path": str(target), "bytes": len(data),
                   "sha256": nyata, "sha256_pendek": nyata[:16]}
            if ingin:
                cocok = nyata.startswith(ingin) or nyata[:16] == ingin
                out.update({"expected": ingin, "cocok": cocok})
                if not cocok:
                    out.update({"ok": False, "error": "sidik tidak cocok — berkas berubah"})
            return out

        if action == "list":
            sec = (config or {}).get("security", {})
            deny = sec.get("denyPaths", [])
            entries = []
            for p in sorted(target.iterdir()):
                # Sembunyikan entri yang path-nya ditolak (mis. folder RAHASIA
                # `skills/hidden`) supaya namanya pun tak terungkap lewat listing.
                if deny and not path_allowed(str(p), deny):
                    continue
                suffix = "/" if p.is_dir() else ""
                size = ""
                try:
                    if p.is_file():
                        size = f" ({p.stat().st_size} B)"
                except Exception:
                    pass
                entries.append(p.name + suffix + size)
            return {"entries": entries[:400], "count": len(entries),
                    "truncated": len(entries) > 400}

        if action == "mkdir":
            target.mkdir(parents=True, exist_ok=True)
            return {"ok": True, "path": str(target)}

        if action == "delete":
            if target.is_dir():
                target.rmdir()  # hanya folder kosong (aman)
            else:
                target.unlink()
            audit("files.delete", path=path)
            return {"ok": True, "path": str(target)}

        return {"error": f"aksi tidak dikenal: {action}"}
    except FileNotFoundError:
        return {"error": f"tidak ditemukan: {path}"}
    except OSError as exc:
        return {"error": f"gagal: {exc}"}
    except Exception as exc:  # noqa: BLE001
        return {"error": f"gagal: {exc}"}


register("files", SCHEMA, handler, toolset="core")
