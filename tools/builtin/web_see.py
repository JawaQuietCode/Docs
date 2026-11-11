"""web_see.py — tool `web_see`: buka halaman, lihat tampilannya, jelaskan.

Bedanya dengan `web_fetch` (teks HTML) dan `browser` (aksi mentah): tool ini
melakukan satu hal yang sering diminta pemilik — "lihat web ini, apa isinya?".
Alurnya: screenshot halaman (Playwright) → kirim ke model vision → kembalikan
deskripsi + teks halaman. Cocok untuk halaman yang isinya dirender JavaScript,
di mana HTML mentah tidak berguna.
"""
from __future__ import annotations

from pathlib import Path

from security.audit import audit
from tools.registry import register

SCHEMA = {
    "description": (
        "Buka sebuah URL, ambil tampilannya (screenshot) dan teksnya, lalu "
        "jelaskan isinya seperti orang melihat layar. Pakai untuk 'lihat web', "
        "'cek halaman ini', atau situs yang isinya dirender JavaScript."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "alamat halaman (http/https)"},
            "question": {"type": "string", "description": "apa yang ingin diketahui dari halaman itu"},
            "full": {"type": "boolean", "description": "screenshot seluruh halaman (bawaan: layar pertama)"},
            "wait_ms": {"type": "integer", "description": "jeda setelah halaman dimuat (ms)"},
        },
        "required": ["url"],
    },
}


def handler(args, config=None):
    args = args or {}
    url = str(args.get("url") or "").strip()
    if not url.startswith(("http://", "https://")):
        return {"error": "url harus http:// atau https://"}
    pertanyaan = str(args.get("question") or "").strip()
    cfg = config or {}

    from tools.builtin import browser as browser_tool
    shot = browser_tool.handler(
        {
            "action": "screenshot",
            "url": url,
            "full": bool(args.get("full")),
            "settleMs": int(args.get("wait_ms") or 1200),
            "waitStable": True,
        },
        cfg,
    )
    if shot.get("error"):
        return {"error": f"gagal membuka halaman: {shot['error']}", "url": url}

    jalur = shot.get("path") or ""
    teks = ""
    try:
        from tools.builtin import browser as bt
        halaman = bt.handler({"action": "text", "limit": 6000}, cfg)
        teks = str(halaman.get("text") or "").strip()
    except Exception:  # noqa: BLE001
        teks = ""

    from tools.builtin import vision as vision_tool
    prompt = (
        (pertanyaan + "\n\n" if pertanyaan else "")
        + "Ini tangkapan layar sebuah halaman web. Jelaskan apa yang terlihat: "
          "judul, bagian utama, isi penting, dan hal yang menonjol."
    )
    lihat = vision_tool.handler({"path": jalur, "prompt": prompt}, cfg) if jalur else {}
    if lihat.get("error") and not teks:
        return {"error": f"tidak bisa membaca tampilan: {lihat['error']}", "url": url}

    hasil = {
        "ok": True,
        "url": shot.get("url") or url,
        "title": shot.get("title") or "",
        "screenshot": jalur,
        "look": lihat.get("text") or "",
        "text": teks[:4000],
    }
    audit("web_see.ok", url=url, bytes=shot.get("sizeBytes"))
    return hasil


register("web_see", SCHEMA, handler, toolset="web")
