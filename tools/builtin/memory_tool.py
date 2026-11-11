"""memory_tool.py — tool `memory`: memori jangka panjang per ruangan.

Memori disimpan sebagai berkas Markdown di dalam RUANGAN owner aktif
(`MEMORY.md` untuk native/hermes, `CLAUDE.md` untuk claude, `AGENTS.md` untuk
codex) — lihat ``memory/roomfiles.py``. Tiap owner punya ruangan sendiri, jadi
memori tidak pernah bercampur.

Sumber kebenaran = index JSON di sebelah cermin (``<Nama>.index.json``); cermin
``.md`` dirender dari index (hanya entri AKTIF). Kebijakan (jenis, TTL, dedup,
gerbang rahasia) ada di ``memory/policy.py``.

Aksi:
  add    — tambah entri (kind/source opsional)
  update — perbarui entri (id ATAU teks lama)
  forget — lupakan (soft; hilang dari prompt, teks dibuang)
  delete — hapus (hard)
  list   — daftar entri (kind?/include_stale?)
  search — cari entri
  read   — baca seluruh cermin memori

Rahasia (token/key/password/JWT) DITOLAK secara bawaan; hanya boleh dengan
``allow_sensitive=true`` (persetujuan eksplisit pemilik).
"""
from memory import policy
from memory import roomfiles
from security.audit import audit
from security.tenant_context import get_current
from tools.registry import register

SCHEMA = {
    "description": (
        "Memori jangka panjang MILIKMU tentang pemilik ini (bertahan lintas sesi). "
        "Simpan fakta/preferensi/keputusan penting; jangan simpan hal sepele atau "
        "rahasia. Jenis: fact (terverifikasi), preference, decision (sementara, "
        "kedaluwarsa 30 hari), guess (dugaan, 7 hari). "
        "Aksi: add (text=…, kind=…), update (id/old=…, text=…), forget (id=…), "
        "delete (id=…), list (kind=…), search (query=…), read."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "action": {"type": "string",
                       "enum": ["add", "update", "forget", "delete", "list", "search", "read"]},
            "text": {"type": "string", "description": "Isi entri (add/update)"},
            "kind": {"type": "string",
                     "description": "fact | preference | decision | guess"},
            "source": {"type": "string",
                       "description": "user | observed | inferred (asal informasi)"},
            "id": {"type": "string", "description": "Id entri (update/forget/delete)"},
            "old": {"type": "string", "description": "Teks lama entri (alternatif id)"},
            "reason": {"type": "string", "description": "Alasan (forget)"},
            "query": {"type": "string", "description": "Kata kunci (search)"},
            "include_stale": {"type": "boolean", "description": "Ikut tampilkan entri usang"},
            "allow_sensitive": {"type": "boolean",
                                "description": "Penyimpanan rahasia (butuh izin eksplisit pemilik)"},
        },
        "required": ["action"],
    },
}


def _room():
    cur = get_current() or {}
    if not cur or not cur.get("root"):
        return None, None
    return cur, cur.get("mechanism") or "native"


def handler(args, config=None):
    args = args or {}
    action = str(args.get("action", "")).strip().lower()
    room, mech = _room()
    if not room:
        return {"error": "tidak ada ruangan aktif (memori per-owner tidak tersedia di sini)"}

    allow = bool(args.get("allow_sensitive"))

    if action == "read":
        txt = roomfiles.read_memory(room, mech)
        return {"ok": True, "mechanism": mech, "file": roomfiles.primary_file(mech),
                "content": txt or "(memori masih kosong)"}

    if action == "list":
        kind = str(args.get("kind", "") or "").strip() or None
        inc = bool(args.get("include_stale"))
        items = roomfiles.list_entries(room, mech, kind=kind, include_stale=inc)
        return {"ok": True, "mechanism": mech, "count": len(items), "facts": items}

    if action == "search":
        q = str(args.get("query", "")).strip()
        if not q:
            return {"error": "query wajib untuk action=search"}
        inc = bool(args.get("include_stale"))
        hits = roomfiles.search_entries(room, mech, q, include_stale=inc)
        return {"ok": True, "mechanism": mech, "query": q, "count": len(hits), "hits": hits}

    if action == "add":
        text = str(args.get("text", "")).strip()
        if not text:
            return {"error": "text wajib untuk action=add"}
        kind = policy.normalize_kind(args.get("kind") or policy.FACT)
        source = policy.normalize_source(args.get("source") or "user")
        res = roomfiles.add_entry(room, mech, text, kind=kind, source=source,
                                  allow_sensitive=allow)
        if res.get("ok"):
            audit("memory.add", mechanism=mech, kind=kind,
                  duplicate=bool(res.get("duplicate")), sensitive=allow)
            # Mem0 is an optional semantic index, never the source of truth.
            # The Markdown room memory is committed first; provider failures do
            # not roll back or invalidate the durable local write.
            mem0_cfg = ((config or {}).get("memory") or {}).get("mem0") or {}
            if mem0_cfg.get("enabled"):
                try:
                    from memory import mem0_backend
                    ctx = (config or {}).get("context") or {}
                    synced = mem0_backend.add(
                        text, tenant=str(ctx.get("tenant") or room.get("tenant") or "default"),
                        config=config, chat_id=str(ctx.get("chat_id") or ""),
                        metadata={"kind": kind, "source": source},
                    )
                    res["mem0_sync"] = "synced" if synced.get("ok") else str(synced.get("status") or "unavailable")
                except Exception:
                    res["mem0_sync"] = "unavailable"
        else:
            audit("memory.reject", mechanism=mech, kind=kind,
                  reason=res.get("error"), secret=bool(res.get("secret")))
        return res

    if action == "update":
        ref = str(args.get("id", "") or args.get("old", "")).strip()
        if not ref:
            return {"error": "id (atau old) wajib untuk action=update"}
        text = args.get("text")
        text = str(text).strip() if text is not None else None
        res = roomfiles.update_entry(room, mech, ref, text=text,
                                     kind=(str(args["kind"]).strip() if args.get("kind") else None),
                                     source=(str(args["source"]).strip() if args.get("source") else None),
                                     allow_sensitive=allow)
        if res.get("ok"):
            audit("memory.update", mechanism=mech, kind=res.get("kind"))
        else:
            audit("memory.reject", mechanism=mech, op="update",
                  reason=res.get("error"), secret=bool(res.get("secret")))
        return res

    if action == "forget":
        ref = str(args.get("id", "") or args.get("old", "")).strip()
        if not ref:
            return {"error": "id (atau old) wajib untuk action=forget"}
        res = roomfiles.forget_entry(room, mech, ref, reason=str(args.get("reason", "") or ""))
        if res.get("ok"):
            audit("memory.forget", mechanism=mech, kind=res.get("kind"))
        return res

    if action == "delete":
        ref = str(args.get("id", "") or args.get("old", "")).strip()
        if not ref:
            return {"error": "id (atau old) wajib untuk action=delete"}
        res = roomfiles.delete_entry(room, mech, ref)
        if res.get("ok"):
            audit("memory.delete", mechanism=mech, kind=res.get("kind"))
        return res

    return {"error": "action tidak dikenal (add/update/forget/delete/list/search/read)"}


register("memory", SCHEMA, handler, toolset="core")
