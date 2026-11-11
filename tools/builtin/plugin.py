"""plugin.py — tool `plugin_write` / `plugin_run` / `plugin_list`.

Kwee bisa MENULIS plugin baru sendiri lalu menjalankannya. Plugin adalah file
`.js` di folder `plugins/` (sisi Node, karena butuh socket WhatsApp). Eksekusi
dilakukan lewat server aksi Node (`transport/api/actions.js` → aksi `plugin_run`),
karena hanya Node yang memegang socket.

Kontrak plugin (lihat transport/plugin-host.js):

    export default {
      name: 'salam',
      description: 'Balas salam',
      commands: ['halo'],
      async run(ctx) { await ctx.wa.send(ctx.msg.chatId, { text: 'hai' }) }
    }

Keamanan: hanya owner; menulis plugin = menjalankan kode → wajib konfirmasi.
"""
import json
import re
import urllib.error
import urllib.request
from pathlib import Path

from security.audit import audit
from tools.registry import register

ROOT = Path(__file__).resolve().parents[2]
PLUGINS_DIR = ROOT / "plugins"

_SAFE_NAME = re.compile(r"^[a-z0-9_\-]{1,40}$")
# Pola yang tidak boleh ada di kode plugin (best-effort; bukan sandbox OS).
_BLOCKED = [
    r"process\.exit",
    r"child_process",
    r"require\(['\"]fs['\"]\)",   # fs sudah tersedia via ctx bila perlu
    r"import\s+.*from\s+['\"]node:fs['\"]",
    r"rm\s+-rf",
]


def _action(cfg, name, args):
    """Panggil server aksi Node (loopback)."""
    sec = (cfg or {}).get("security") or {}
    host = sec.get("actionHost", "127.0.0.1")
    port = sec.get("actionPort", 8788)
    import os
    token = os.environ.get("ACTION_TOKEN", "")
    if not token:
        return {"error": "ACTION_TOKEN kosong; aksi plugin dimatikan"}
    body = json.dumps({"action": name, "args": args}).encode()
    req = urllib.request.Request(
        f"http://{host}:{port}/action", data=body,
        headers={"Content-Type": "application/json", "x-kwee-token": token},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=40) as resp:
            return json.loads(resp.read().decode() or "{}")
    except urllib.error.HTTPError as exc:
        return {"error": f"HTTP {exc.code}: {exc.read().decode()[:200]}"}
    except Exception as exc:  # noqa: BLE001
        return {"error": f"server aksi tidak bisa dihubungi: {exc}"}


WRITE_SCHEMA = {
    "description": (
        "Tulis plugin JavaScript baru ke folder plugins/ lalu (opsional) langsung "
        "aktifkan. Plugin bisa memakai API WhatsApp penuh lewat ctx: "
        "ctx.wa.send(to, content), ctx.wa.sendRich(to, blocks), ctx.airich.*, "
        "ctx.msg (info pesan), ctx.args, ctx.log(), ctx.state (penyimpanan). "
        "Kontrak: export default { name, description, commands?, async run(ctx){} }."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "Nama plugin (huruf kecil, angka, -/_)"},
            "code": {"type": "string", "description": "Isi file plugin (JS, ES module)"},
            "reload": {"type": "boolean", "description": "Muat ulang daftar plugin setelah menulis (default true)"},
        },
        "required": ["name", "code"],
    },
}

RUN_SCHEMA = {
    "description": "Jalankan plugin yang sudah ada di plugins/ dan kembalikan hasilnya.",
    "parameters": {
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "Nama plugin"},
            "args": {"type": "object", "description": "Argumen untuk plugin (opsional)"},
            "to": {"type": "string", "description": "Chat tujuan (default: chat saat ini)"},
        },
        "required": ["name"],
    },
}

LIST_SCHEMA = {
    "description": "Daftar plugin yang ada di plugins/ beserta deskripsi dan perintahnya.",
    "parameters": {"type": "object", "properties": {}},
}


def _owner_mode(config) -> bool:
    """Mode owner: pemilik sendiri yang meminta, jadi tidak perlu gerbang ganda."""
    sec = (config or {}).get("security") or {}
    return str(sec.get("accessMode") or "owner").strip().lower() == "owner" and not sec.get("pluginRequireConfirm")


def write_handler(args, config=None):
    args = args or {}
    ctx = (config or {}).get("context") or {}
    if not ctx.get("owner_confirmed") and not _owner_mode(config):
        return {"error": "menulis plugin ditahan: pemilik belum menyatakan setuju di pesan terakhirnya."}
    name = str(args.get("name", "")).strip().lower()
    code = str(args.get("code", ""))
    if not _SAFE_NAME.match(name):
        return {"error": "nama plugin harus [a-z0-9_-], maksimal 40 karakter"}
    if not code.strip():
        return {"error": "kode plugin kosong"}
    for pat in _BLOCKED:
        if re.search(pat, code):
            return {"error": f"kode plugin memuat pola terlarang: {pat}"}

    PLUGINS_DIR.mkdir(parents=True, exist_ok=True)
    path = PLUGINS_DIR / f"{name}.js"
    path.write_text(code, encoding="utf-8")
    audit("plugin.write", name=name, bytes=len(code))
    out = {"ok": True, "name": name, "path": str(path)}
    # Muat ulang daftar plugin di sisi Node (agar langsung aktif).
    if args.get("reload", True):
        out["reload"] = _action(config, "plugin_reload", {})
    return out


def run_handler(args, config=None):
    args = args or {}
    name = str(args.get("name", "")).strip().lower()
    if not name:
        return {"error": "nama plugin wajib"}
    to = args.get("to") or ((config or {}).get("context") or {}).get("chat_id")
    return _action(config, "plugin_run", {"name": name, "args": args.get("args") or {}, "to": to})


def list_handler(args, config=None):
    return _action(config, "plugin_list", {})


register("plugin_write", WRITE_SCHEMA, write_handler, toolset="core", dangerous=True)
register("plugin_run", RUN_SCHEMA, run_handler, toolset="core", dangerous=False)
register("plugin_list", LIST_SCHEMA, list_handler, toolset="core", dangerous=False)
