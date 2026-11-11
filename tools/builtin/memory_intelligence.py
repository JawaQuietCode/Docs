"""Native memory intelligence tool; tenant local and independent of plugins."""
from pathlib import Path
from memory.intelligence import IntelligentMemory, KINDS
from security.tenant_context import get_current
from tools.registry import register
SCHEMA={"description":"Native adaptive memory: add/search/retrieve/link/compress/reflect/provenance across episodic, semantic, procedural, working, long-term, associative, temporal, and hierarchical memory. No ! command/plugin required.","parameters":{"type":"object","properties":{"action":{"type":"string","enum":["add","search","retrieve","link","compress","reflect","provenance"]},"kind":{"type":"string","enum":sorted(KINDS)},"content":{"type":"string"},"query":{"type":"string"},"source":{"type":"string"},"metadata":{"type":"object"},"ttl_seconds":{"type":"integer","minimum":0},"id":{"type":"integer"},"other_id":{"type":"integer"},"label":{"type":"string"},"limit":{"type":"integer","minimum":1,"maximum":100}},"required":["action"]}}
def handler(args,config=None):
    ctx=get_current() or {}; root=ctx.get("root")
    if not root:return {"ok":False,"error":"no active tenant room"}
    path=Path(root)/"memory"/"intelligence.sqlite3"; store=IntelligentMemory(path)
    try:
        a=args or {}; action=a.get("action")
        if action=="add": return store.add(a.get("kind"),a.get("content"),source=a.get("source","user"),metadata=a.get("metadata"),ttl_seconds=a.get("ttl_seconds",0))
        if action in {"search","retrieve"}: return {"ok":True,"items":store.search(a.get("query"),kinds=[a["kind"]] if a.get("kind") else None,limit=a.get("limit",8))}
        if action=="link": return store.link(a.get("id"),a.get("other_id"),a.get("label","related"))
        if action=="compress": return store.compress(kind=a.get("kind"),limit=a.get("limit",100))
        if action=="reflect": return store.reflect(a.get("limit",20))
        if action=="provenance": return store.provenance(a.get("id"))
        return {"ok":False,"error":"unknown action"}
    finally: store.close()
register("memory_intelligence",SCHEMA,handler,toolset="core")
