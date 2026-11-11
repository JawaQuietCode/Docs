import json
import re
from pathlib import Path

from security.audit import audit
from tools.registry import register

ROOT = Path(__file__).resolve().parents[2]
PLUGINS_DIR = ROOT / "plugins"
STATE_FILE = ROOT / "data" / "plugins" / ".system" / "enabled.json"
_SAFE_NAME = re.compile(r"^[a-z0-9_-]{1,40}$")

SCHEMA = {
    "description": "Lihat atau ubah status aktif plugin. Aksi enable/disable butuh persetujuan eksplisit owner.",
    "parameters": {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["list", "enable", "disable"]},
            "name": {"type": "string", "description": "Nama plugin tanpa ekstensi .js"},
        },
        "required": ["action"],
    },
}


def _read():
    try:
        value = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def handler(args, config=None):
    args = args or {}
    action = str(args.get("action", "")).strip().lower()
    files = sorted(p.stem for p in PLUGINS_DIR.glob("*.js") if not p.name.startswith("_"))
    state = _read()
    if action == "list":
        return {"ok": True, "plugins": [{"name": n, "enabled": state.get(n, True) is not False} for n in files]}
    if action not in {"enable", "disable"}:
        return {"error": "action harus list, enable, atau disable"}
    name = str(args.get("name", "")).strip().lower()
    if not _SAFE_NAME.fullmatch(name):
        return {"error": "name plugin tidak valid"}
    if name not in files:
        return {"error": f"plugin '{name}' tidak ditemukan"}
    ctx = (config or {}).get("context") or {}
    sec = (config or {}).get("security") or {}
    owner = str(sec.get("accessMode") or "owner").strip().lower() == "owner"
    if not ctx.get("owner_confirmed") and not (owner and not sec.get("pluginRequireConfirm")):
        return {"error": f"{action} plugin ditahan: minta persetujuan eksplisit owner terlebih dahulu"}
    state[name] = action == "enable"
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    temp = STATE_FILE.with_suffix(".json.tmp")
    temp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(STATE_FILE)
    audit("plugin.toggle", name=name, enabled=state[name])
    return {"ok": True, "name": name, "enabled": state[name]}


register("plugin_control", SCHEMA, handler, toolset="core", dangerous=False)
