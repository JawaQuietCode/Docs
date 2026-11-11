#!/usr/bin/env python3
"""plugins/_tourl.py — helper Python untuk plugin `tourl`.

Dua pekerjaan, keduanya pakai stdlib saja:
  1. ekstrak URL dari teks (caption pesan yang di-reply)
  2. upload berkas media ke file host sementara → jadi URL yang bisa diunduh

Output JSON di stdout: {"urls": [...], "upload": "https://..." | null, "error": "..."}

Dipanggil dari plugins/tourl.js (Node) — bukan plugin mandiri.
"""
from __future__ import annotations

import argparse
import json
import mimetypes
import os
import re
import sys
import urllib.error
import urllib.request

# URL umum: http/https + karakter yang BOLEH ada di URL. Titik koma/koma/
# tanda baca di ekor dibuang terpisah (biasanya bukan bagian URL).
URL_RE = re.compile(r"https?://[^\s<>\"'{}|\\^`\[\]]+")
TRAIL = ".,;:!?)]}>\"'"

# Host sementara. Urutan = urutan percobaan. Field = nama field multipart.
#   x0.at  → field "file", balas URL teks biasa
#   uguu   → field "files[]", balas URL teks biasa (?output=text)
HOSTS = [
    ("https://x0.at", "file"),
    ("https://uguu.se/upload?output=text", "files[]"),
]

MAX_BYTES = 100 * 1024 * 1024


def extract_urls(text: str) -> list[str]:
    """URL unik dari teks, ekor tanda baca dibuang."""
    out: list[str] = []
    for m in URL_RE.finditer(text or ""):
        url = m.group(0).rstrip(TRAIL)
        if url and url not in out:
            out.append(url)
    return out


def _multipart(field: str, filename: str, content: bytes, mime: str) -> tuple[bytes, str]:
    """Bentuk body multipart/form-data manual (stdlib saja)."""
    boundary = "----kwee" + os.urandom(12).hex()
    head = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="{field}"; filename="{filename}"\r\n'
        f"Content-Type: {mime}\r\n\r\n"
    ).encode()
    tail = f"\r\n--{boundary}--\r\n".encode()
    return head + content + tail, f"multipart/form-data; boundary={boundary}"


def upload(path: str) -> tuple[str | None, str | None]:
    """Upload berkas ke host sementara. Balas (url, error)."""
    if not os.path.isfile(path):
        return None, f"berkas tidak ditemukan: {path}"
    size = os.path.getsize(path)
    if size == 0:
        return None, "berkas kosong"
    if size > MAX_BYTES:
        return None, f"berkas terlalu besar ({size} B, maks {MAX_BYTES} B)"

    with open(path, "rb") as fh:
        content = fh.read()

    name = os.path.basename(path) or "file"
    mime = mimetypes.guess_type(name)[0] or "application/octet-stream"
    errors = []

    for url, field in HOSTS:
        body, ctype = _multipart(field, name, content, mime)
        req = urllib.request.Request(
            url, data=body, headers={"Content-Type": ctype}, method="POST"
        )
        try:
            with urllib.request.urlopen(req, timeout=90) as resp:
                text = (resp.read().decode("utf-8", "replace") or "").strip()
        except urllib.error.HTTPError as exc:
            errors.append(f"{url}: HTTP {exc.code}")
            continue
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{url}: {exc}")
            continue

        # Semua host di HOSTS membalas URL dalam teks biasa.
        link = ""
        for line in reversed(text.splitlines()):
            line = line.strip()
            if line.startswith("http"):
                link = line
                break
        if link:
            return link, None
        errors.append(f"{url}: balasan tak dikenal: {text[:120]}")

    return None, "; ".join(errors) or "semua host gagal"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--text", default="")
    ap.add_argument("--file", default="")
    args = ap.parse_args()

    result = {"urls": extract_urls(args.text), "upload": None, "error": None}

    if args.file:
        link, err = upload(args.file)
        if link:
            result["upload"] = link
        else:
            result["error"] = err

    sys.stdout.write(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
