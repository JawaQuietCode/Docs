"""Research source ledger and field-notes tools, scoped to the active chat."""
from __future__ import annotations

from memory import manager as store_manager
from security.tenant_context import get_current
from tools.registry import register

SCHEMA = {
    "description": (
        "Perpustakaan riset Kwee untuk chat aktif: cari sumber yang pernah dibuka, "
        "lihat sumber terbaru, simpan catatan jurnal yang punya provenance, dan "
        "lihat efisiensi tools. Sumber mentah tetap scoped ke chat aktif. Catatan dapat "
        "disimpan ke chat (default) atau tenant library (lintas-chat) bila owner meminta "
        "berbagi/menyimpan; catatan peer-learning otomatis diberi label unverified. "
        "Aksi: search_sources(query), recent_sources(limit), save_note(title,content,tags,source_url,scope), "
        "search_notes(query), stats. Jangan menganggap snippet sebagai verifikasi final."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["search_sources", "recent_sources", "save_note", "search_notes", "stats"]},
            "query": {"type": "string", "description": "Kata kunci sumber/catatan"},
            "title": {"type": "string", "description": "Judul catatan lapangan"},
            "content": {"type": "string", "description": "Isi catatan (cantumkan fakta, analisis, dan ketidakpastian secara terpisah)"},
            "tags": {"type": "array", "items": {"type": "string"}},
            "source_url": {"type": "string", "description": "URL sumber utama (opsional)"},
            "scope": {"type": "string", "enum": ["chat", "library"], "description": "chat (default) atau library tenant, hanya atas permintaan eksplisit owner"},
            "limit": {"type": "integer", "description": "Jumlah hasil maksimum"},
        },
        "required": ["action"],
    },
}


def handler(args, config=None):
    args = args or {}
    action = str(args.get("action") or "").strip().lower()
    ctx = (config or {}).get("context") or {}
    chat_id = str(ctx.get("chat_id") or "").strip()
    tenant = str(ctx.get("tenant") or "").strip()
    if not chat_id:
        return {"ok": False, "error": "active chat context is unavailable"}
    store = store_manager.store_for(tenant, config or {})
    limit = max(1, min(20, int(args.get("limit") or 8)))

    if action == "search_sources":
        query = str(args.get("query") or "").strip()
        if not query:
            return {"ok": False, "error": "query wajib"}
        rows = store.search_sources(chat_id, query, limit=limit)
        return {"ok": True, "scope": "active_chat", "count": len(rows), "sources": rows}
    if action == "recent_sources":
        rows = store.recent_sources(chat_id, limit=limit)
        return {"ok": True, "scope": "active_chat", "count": len(rows), "sources": rows}
    if action == "search_notes":
        query = str(args.get("query") or "").strip()
        if not query:
            return {"ok": False, "error": "query wajib"}
        rows = store.search_journal_notes(chat_id, query, limit=limit, include_library=True)
        return {"ok": True, "scope": "active_chat_plus_curated_tenant_library", "count": len(rows), "notes": rows}
    if action == "save_note":
        result = store.save_journal_note(
            chat_id, args.get("title"), args.get("content"),
            tags=args.get("tags") or [], source_url=args.get("source_url") or "",
            scope=str(args.get("scope") or "chat").strip().lower(),
        )
        return {**result, "scope": result.get("scope", "chat") if result.get("ok") else "unchanged"}
    if action == "stats":
        return {
            "ok": True,
            "scope": "tenant_tools_and_active_chat_sources_and_curated_library",
            "recent_sources": store.recent_sources(chat_id, limit=5),
            "tool_efficiency": store.tool_stats(limit=12),
        }
    return {"ok": False, "error": "action tidak dikenal"}


register("research_journal", SCHEMA, handler, toolset="core", requires_owner=True)
