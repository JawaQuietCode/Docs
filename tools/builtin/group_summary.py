import hashlib
import json
import os
import time
from pathlib import Path

from security.tenant_context import get_current
from tools.registry import register

ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = ROOT / "data" / "group-summary"

SCHEMA = {
    "description": "Ambil pesan teks 24 jam terakhir untuk ringkasan grup yang telah diaktifkan owner. Hanya untuk job/percakapan pada grup yang sama; tidak membaca grup yang tidak opt-in.",
    "parameters": {
        "type": "object",
        "properties": {},
        "required": [],
    },
}


def handler(args, config=None):
    ctx = (config or {}).get("context") or {}
    chat_id = str(ctx.get("chat_id", ""))
    if not chat_id.endswith("@g.us"):
        return {"error": "tool ini hanya tersedia di chat grup WhatsApp"}
    settings_path = DATA_DIR / "settings.json"
    try:
        settings = json.loads(settings_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        settings = {}
    group = (settings.get("groups") or {}).get(chat_id) or {}
    if not group.get("enabled"):
        return {"error": "ringkasan grup belum diaktifkan owner; aktifkan dengan !ringkasan on HH:MM di grup"}
    group_hash = hashlib.sha256(chat_id.encode()).hexdigest()
    cutoff = int(time.time()) - 86400
    retention_cutoff = time.time() - 7 * 86400
    rows = []
    for path in sorted(DATA_DIR.glob(f"{group_hash}_*.jsonl")):
        try:
            if path.stat().st_mtime < retention_cutoff:
                path.unlink(missing_ok=True)
                continue
            for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
                try:
                    row = json.loads(line)
                    if int(row.get("ts", 0)) >= cutoff and str(row.get("text", "")).strip():
                        rows.append(row)
                except (ValueError, TypeError):
                    continue
        except OSError:
            continue
    rows.sort(key=lambda row: int(row.get("ts", 0)))
    rows = rows[-500:]
    if not rows:
        return {"ok": True, "count": 0, "messages": [], "retention_days": 7}
    messages = [{"time": time.strftime("%H:%M", time.localtime(int(row["ts"]))), "participant": str(row.get("sender", "anggota")), "text": str(row.get("text", ""))[:1800]} for row in rows]
    return {"ok": True, "count": len(messages), "messages": messages, "retention_days": 7}


register("group_summary", SCHEMA, handler, toolset="core", dangerous=False)
