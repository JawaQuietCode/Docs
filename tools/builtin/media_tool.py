"""media_tool.py — tool `media`: kelola penyimpanan aset (gambar/video/audio/dokumen).

Agent memakai ini untuk: melihat daftar aset, menyimpan berkas (hasil render,
unduhan, screenshot) ke penyimpanan permanen per tenant, mencari aset lama, atau
menyalin aset ke workspace. Lampiran WhatsApp yang masuk OTOMATIS tersimpan
(dilakukan transport), jadi biasanya cukup `list`/`get` lalu kirim via `wa`.
"""
from __future__ import annotations

import json

from tools.registry import register

SCHEMA = {
    "description": (
        "Kelola aset media (gambar/video/audio/dokumen) yang tersimpan per pemilik. "
        "Aksi: list (daftar terbaru, filter kind/query), get (metadata satu aset), "
        "save (simpan berkas lokal ke penyimpanan media), resolve (cari path dari id). "
        "Lampiran dari WhatsApp sudah tersimpan otomatis; pakai `list` untuk melihatnya."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "action": {"type": "string", "description": "list | get | save | resolve"},
            "kind": {"type": "string", "description": "image|video|audio|document|sticker (untuk list)"},
            "query": {"type": "string", "description": "kata kunci pencarian (untuk list)"},
            "limit": {"type": "integer", "description": "jumlah maksimum (default 30)"},
            "id": {"type": "string", "description": "id aset (m_xxxx) untuk get/resolve"},
            "path": {"type": "string", "description": "path berkas untuk save"},
            "note": {"type": "string", "description": "keterangan opsional saat menyimpan"},
        },
        "required": ["action"],
    },
}


def _short(it: dict) -> dict:
    """Ringkas metadata supaya hemat token di konteks LLM."""
    return {
        "id": it.get("id"),
        "kind": it.get("kind"),
        "mime": it.get("mime"),
        "filename": it.get("filename"),
        "bytes": it.get("bytes"),
        "note": it.get("note", ""),
        "source": it.get("source", ""),
    }


def handler(args, config=None):
    args = args or {}
    action = str(args.get("action") or "list").strip().lower()
    try:
        from media import store as media_store
    except Exception as exc:  # noqa: BLE001
        return {"error": f"penyimpanan media tidak tersedia: {exc}"}

    if action == "list":
        items = media_store.list_assets(
            kind=str(args.get("kind") or ""),
            limit=int(args.get("limit") or 30),
            query=str(args.get("query") or ""),
        )
        return {"ok": True, "count": len(items), "items": [_short(it) for it in items]}

    if action == "get":
        it = media_store.get_asset(str(args.get("id") or ""))
        if not it:
            return {"error": "aset tidak ditemukan"}
        return {"ok": True, "asset": it}

    if action == "resolve":
        p = media_store.resolve_path(str(args.get("id") or args.get("path") or ""))
        if not p:
            return {"error": "path tidak ditemukan"}
        return {"ok": True, "path": p}

    if action == "save":
        src = str(args.get("path") or "").strip()
        if not src:
            return {"error": "path kosong"}
        entry = media_store.save_file(src, note=str(args.get("note") or ""))
        if entry.get("error"):
            return entry
        return {"ok": True, "asset": _short(entry)}

    return {"error": f"aksi tidak dikenal: {action}"}


register("media", SCHEMA, handler, toolset="core", dangerous=False)
