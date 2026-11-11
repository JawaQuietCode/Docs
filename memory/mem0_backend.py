"""Optional Mem0 OSS backend with tenant-isolated vector collections.

SQLite/roomfiles remain the local source of truth. Mem0 augments retrieval only
when explicitly enabled and fully configured; this module never falls back to
Mem0's implicit OpenAI defaults.
"""
from __future__ import annotations

import copy
import hashlib
import inspect
import threading
from importlib import metadata

_LOCK = threading.RLock()
_MEMORIES: dict[tuple[str, str], object] = {}


def _tenant_id(tenant: str, chat_id: str = "") -> str:
    # A chat-scoped user id prevents group/private-chat recall from crossing.
    raw = f"{tenant or 'default'}\0{chat_id or 'default'}"
    return "kwee_" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def _settings(config: dict | None) -> dict:
    root = (config or {}).get("memory") or {}
    settings = root.get("mem0") or {}
    return settings if isinstance(settings, dict) else {}


def status(config: dict | None = None) -> dict:
    settings = _settings(config)
    if not settings.get("enabled", False):
        return {"enabled": False, "available": False, "status": "disabled", "provider": "mem0-oss"}
    try:
        oss = settings.get("oss_config") or settings.get("ossConfig") or {}
        vector_store = oss.get("vector_store") or {}
        vector_config = vector_store.get("config") or {}
        llm = oss.get("llm") or {}
        embedder = oss.get("embedder") or {}
        if not vector_store.get("provider") or not vector_config.get("host") or not vector_config.get("port"):
            raise ValueError("OSS config needs vector_store.provider and Qdrant host/port")
        if not llm.get("provider") or not embedder.get("provider"):
            raise ValueError("OSS config needs explicit llm and embedder providers")
    except Exception as exc:
        return {"enabled": True, "available": False, "status": "incomplete_config", "error": str(exc)}
    try:
        version = metadata.version("mem0ai")
    except Exception:
        return {"enabled": True, "available": False, "status": "missing_package", "provider": "mem0-oss"}
    return {"enabled": True, "available": True, "status": "ready", "version": version,
            "vector_store": str(vector_store.get("provider")),
            "llm_provider": str(llm.get("provider")),
            "embedder_provider": str(embedder.get("provider"))}


def _memory_for(tenant: str, config: dict | None):
    state = status(config)
    if not state.get("available"):
        raise RuntimeError(state.get("error") or f"Mem0 {state.get('status')}")
    settings = _settings(config)
    oss = copy.deepcopy(settings.get("oss_config") or settings.get("ossConfig") or {})
    vector = oss.setdefault("vector_store", {}).setdefault("config", {})
    base_name = str(settings.get("collection_name") or settings.get("collectionName") or "kwee_memories")
    vector["collection_name"] = f"{base_name[:30]}_{hashlib.sha256(str(tenant or 'default').encode()).hexdigest()[:12]}"
    config_key = hashlib.sha256(repr(sorted(oss.items())).encode()).hexdigest()
    cache_key = (str(tenant or "default"), config_key)
    with _LOCK:
        if cache_key in _MEMORIES:
            return _MEMORIES[cache_key]
        try:
            from mem0 import Memory
        except Exception as exc:
            raise RuntimeError("mem0ai tidak dapat diimpor") from exc
        # from_config dipakai supaya provider LLM/embedder/vector store eksplisit;
        # Memory() default dapat diam-diam memakai provider OpenAI.
        memory = Memory.from_config(oss)
        _MEMORIES[cache_key] = memory
        return memory


def _safe_metadata(metadata: dict | None) -> dict:
    """Allow a tiny metadata vocabulary; never persist chat identifiers or secrets."""
    source = metadata if isinstance(metadata, dict) else {}
    return {k: str(source[k])[:160] for k in ("kind", "source") if source.get(k) is not None}


def add(text: str, tenant: str, config: dict | None = None, metadata: dict | None = None,
        chat_id: str = "") -> dict:
    value = str(text or "").strip()
    if not value:
        return {"ok": False, "status": "empty"}
    if not str(chat_id or "").strip():
        return {"ok": False, "status": "missing_chat_context"}
    try:
        memory = _memory_for(tenant, config)
        result = memory.add([{"role": "user", "content": value}], user_id=_tenant_id(tenant, chat_id),
                            metadata=_safe_metadata(metadata))
        return {"ok": True, "result": result}
    except Exception as exc:
        return {"ok": False, "status": "error", "error": str(exc)[:400]}


def search(query: str, tenant: str, config: dict | None = None, limit: int = 5,
           chat_id: str = "") -> dict:
    question = str(query or "").strip()
    if not question:
        return {"ok": False, "results": []}
    if not str(chat_id or "").strip():
        return {"ok": False, "status": "missing_chat_context", "results": []}
    try:
        memory = _memory_for(tenant, config)
        method = memory.search
        params = inspect.signature(method).parameters
        kwargs = {"limit": max(1, min(10, int(limit or 5)))}
        if "user_id" in params:
            kwargs["user_id"] = _tenant_id(tenant, chat_id)
        elif "filters" in params:
            kwargs["filters"] = {"user_id": _tenant_id(tenant, chat_id)}
        result = method(question, **kwargs)
        if isinstance(result, dict):
            hits = result.get("results") or result.get("memories") or []
        else:
            hits = result if isinstance(result, list) else []
        safe_hits = []
        for item in hits[:kwargs["limit"]]:
            if isinstance(item, dict):
                safe_hits.append({k: item.get(k) for k in ("id", "memory", "text", "score", "created_at") if item.get(k) is not None})
            else:
                safe_hits.append({"memory": str(item)[:1000]})
        return {"ok": True, "results": safe_hits}
    except Exception as exc:
        return {"ok": False, "status": "error", "error": str(exc)[:400], "results": []}


def reset():
    """Forget in-process provider objects (used by tests and maintenance)."""
    with _LOCK:
        _MEMORIES.clear()
