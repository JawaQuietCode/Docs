"""Persistent mission tracking as a native agent tool, not a chat command/plugin."""
from pathlib import Path
from memory.intelligence import IntelligentMemory
from security.tenant_context import get_current
from tools.registry import register
SCHEMA={"description":"Persistent Mission Center. Create, list, and update long-running missions stored in the active owner's local memory database. Native tool; no ! command or plugin.","parameters":{"type":"object","properties":{"action":{"type":"string","enum":["create","list","update"]},"title":{"type":"string"},"goal":{"type":"string"},"mission_id":{"type":"integer"},"status":{"type":"string","enum":["active","paused","completed","cancelled"]},"owner":{"type":"string"},"metadata":{"type":"object"},"limit":{"type":"integer","minimum":1,"maximum":100}},"required":["action"]}}
def handler(args,config=None):
    room=get_current() or {}; root=room.get("root")
    if not root:return {"ok":False,"error":"no active tenant room"}
    db=IntelligentMemory(Path(root)/"memory"/"intelligence.sqlite3")
    try:
        a=args or {}
        return db.mission(a.get("action"),mission_id=a.get("mission_id"),title=a.get("title",""),goal=a.get("goal",""),status=a.get("status","active"),owner=a.get("owner",""),metadata=a.get("metadata"),limit=a.get("limit",20))
    finally: db.close()
register("mission_center",SCHEMA,handler,toolset="core")
