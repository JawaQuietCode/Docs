"""wa.py — tool aksi WhatsApp (kirim, grup, profil, presence).

Kecerdasan ada di Python, tapi socket WhatsApp ada di Node. Tool ini jembatan:
ia memanggil server aksi loopback (`transport/api/actions.js`) lewat HTTP.

Keamanan berlapis:
  - server aksi hanya bind 127.0.0.1 + wajib token (`ACTION_TOKEN`)
  - aksi merusak (kick/promote/ubah grup/hapus kontak) WAJIB `confirm: true`
  - daftar aksi di sini mencerminkan `DESTRUCTIVE` di actions.js
"""
import json
import os
import urllib.error
import urllib.request

from security.audit import audit
from tools.registry import register

# Aksi yang mengubah keadaan → wajib confirm=true.
DESTRUCTIVE = {
    "add", "kick", "promote", "demote", "set_subject", "set_description", "leave",
    "create_group", "revoke_invite", "remove_contact", "profile_pic",
    "profile_name", "profile_status", "block", "unblock",
    "ephemeral", "announce", "locked", "approve", "reject", "delete_message",
    "join",
}

SCHEMA = {
    "description": (
        "Lakukan aksi WhatsApp sebagai bot: kirim pesan, KIRIM MEDIA dari disk "
        "(`send_file` dengan args.path — video/gambar/audio/dokumen dikenali dari "
        "ekstensi; video besar dipadatkan otomatis, dan bila masih di atas batas "
        "media WhatsApp ia dikirim sebagai dokumen), balas, reaksi, forward, aksi grup "
        "(kick/promote/demote/ubah nama & deskripsi/undangan/keluar/MASUK lewat "
        "link undangan `join`), profil, dan presence. Aksi yang mengubah data "
        "butuh confirm=true."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "description": "Nama aksi",
                "enum": [
                    "send", "send_file", "rich", "forward", "react", "edit", "delete_message",
                    "kick", "promote", "demote", "set_subject", "set_description",
                    "group_meta", "invite_code", "invite_info", "revoke_invite",
                    "leave", "join",
                    "create_group", "announce", "locked", "ephemeral",
                    "join_requests", "approve", "reject",
                    "profile_pic", "profile_name", "profile_status",
                    "remove_contact", "add_contact", "check_number", "block", "unblock",
                    "typing", "online", "paused", "read",
                ],
            },
            "args": {"type": "object", "description": "Argumen aksi (mis. {group, targets, confirm})"},
        },
        "required": ["action"],
    },
}


def _endpoint(config) -> tuple:
    """Tentukan (base_url, token) server aksi.

    Env MENANG atas YAML (konvensi yang sama dengan transport/env.js dan
    agent/config.py) supaya nilai bisa ditimpa tanpa menyunting config.
    """
    sec = (config or {}).get("security", {}) or {}
    host = os.environ.get("ACTION_HOST") or sec.get("actionHost") or "127.0.0.1"
    port = os.environ.get("ACTION_PORT") or sec.get("actionPort") or "8788"
    token = os.environ.get("ACTION_TOKEN", "")
    return f"http://{host}:{int(port)}", token


def _looks_like_jid(v) -> bool:
    """True bila nilai tampak JID dengan bagian pengguna (mis. '123@g.us')."""
    s = str(v or "").strip()
    return bool(s) and "@" in s and bool(s.split("@")[0])


# Aksi yang beroperasi pada grup/chat.
_GROUP_ACTIONS = {
    "kick", "promote", "demote", "set_subject", "set_description",
    "group_meta", "invite_code", "revoke_invite", "leave", "announce",
    "locked", "ephemeral", "join_requests", "approve", "reject",
}
_CHAT_ACTIONS = {"send", "send_file", "rich", "forward", "react", "edit", "delete_message",
                 "typing", "online", "paused", "read"}


def handler(args, config=None):
    args = args or {}
    action = str(args.get("action", "")).strip()
    if not action:
        return {"error": "action kosong"}
    payload = dict(args.get("args") or {})

    # Isi otomatis dari konteks percakapan ini (supaya AI tidak perlu tahu chat_id).
    # Nilai yang diberikan model tapi BUKAN JID sah (mis. "g.us", "grup ini")
    # diabaikan — kalau tidak, aksi bisa menyasar grup yang salah/rusak.
    ctx = (config or {}).get("context") or {}
    chat_id = ctx.get("chat_id")
    if action in _GROUP_ACTIONS:
        if not _looks_like_jid(payload.get("group")):
            payload["group"] = chat_id
    if action in _CHAT_ACTIONS:
        if not _looks_like_jid(payload.get("to")):
            payload["to"] = chat_id
        if not _looks_like_jid(payload.get("chat_id")):
            payload["chat_id"] = chat_id

    sec = (config or {}).get("security", {}) or {}
    owner = str(sec.get("accessMode") or "owner").strip().lower() == "owner"
    butuh_konfirmasi = (not owner) or bool(sec.get("waRequireConfirm", False))
    if owner and not butuh_konfirmasi:
        # Mode owner: pemilik menjalankan aksinya sendiri dari DM-nya. Tidak ada
        # gerbang konfirmasi ganda — itu sumber keluhan "tidak diizinkan".
        if action in DESTRUCTIVE:
            payload["confirm"] = True
            payload["__owner_confirmed"] = True
    elif action in DESTRUCTIVE and payload.get("confirm") is not True:
        return {
            "error": (
                f"aksi '{action}' mengubah data. Konfirmasi dulu ke pemilik, "
                "lalu ulangi dengan args.confirm=true."
            )
        }
    # Gerbang kedua: persetujuan HARUS berasal dari pesan pemilik, bukan dari
    # model yang menulis confirm=true sendiri. Tanpa ini, model bisa
    # "menyetujui dirinya sendiri" lalu menendang orang tanpa izin.
    if butuh_konfirmasi and action in DESTRUCTIVE and not ctx.get("owner_confirmed"):
        return {
            "error": (
                f"aksi '{action}' ditahan: pemilik belum menyatakan setuju di pesan "
                "terakhirnya. Tanya dulu ('Setuju saya kick X?'), tunggu jawaban ya, "
                "baru jalankan. Kata 'ya/setuju/gas' dari pemilik akan membuka gerbang ini."
            )
        }
    # Teruskan bukti persetujuan ke server aksi (dipakai gerbang kedua di Node).
    if action in DESTRUCTIVE:
        payload["__owner_confirmed"] = True

    base, token = _endpoint(config)
    if not token:
        return {"error": "ACTION_TOKEN belum diset — aksi WhatsApp dinonaktifkan"}

    body = json.dumps({"action": action, "args": payload}).encode()
    req = urllib.request.Request(
        f"{base}/action", data=body,
        headers={"content-type": "application/json", "x-kwee-token": token},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            out = json.loads(r.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        detail = e.read().decode()[:200] if hasattr(e, "read") else ""
        audit("wa.action", action=action, ok=False, http=e.code)
        return {"error": f"server aksi menolak ({e.code}): {detail}"}
    except Exception as e:  # noqa: BLE001
        audit("wa.action", action=action, ok=False, error=str(e))
        return {"error": f"tidak bisa menghubungi server aksi: {e}"}

    audit("wa.action", action=action, ok=not (isinstance(out, dict) and out.get("error")))
    return out


register("wa", SCHEMA, handler, toolset="core", dangerous=True)
