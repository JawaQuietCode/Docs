"""Safe status report for Kwee's optional intelligence integrations."""
from __future__ import annotations
import shutil
from pathlib import Path
from integrations import langchain_adapter
from integrations.mcp_bridge import bridge_from_config, list_server_configs
from memory import mem0_backend
from tools.registry import register

SCHEMA = {
    "description": "Periksa status modul jurnal/perpustakaan, LangChain, Mem0 OSS, Graphify, dan MCP GitHub tanpa menampilkan secrets.",
    "parameters": {"type": "object", "properties": {}, "required": []},
}


def handler(args=None, config=None):
    cfg = config or {}
    root = Path(cfg.get("root") or ".").resolve()
    graph_path = root / "graphify-out" / "graph.json"
    servers = list_server_configs(cfg)
    bridge = bridge_from_config(cfg)
    mcp_status = []
    for name, server in sorted(servers.items()):
        if not isinstance(server, dict):
            continue
        mcp_status.append(bridge.server_status(name, server, cfg))
    return {
        "ok": True,
        "library": {"sqlite_fts": True, "source_journal": True, "tool_learning": True},
        "langchain": langchain_adapter.status(),
        "mem0": mem0_backend.status(cfg),
        "graphify": {
            "cli": shutil.which("graphify") is not None,
            "graph_file": graph_path.is_file(),
            "mcp": next((x for x in mcp_status if x.get("name") == "graphify"), {"status": "not_configured"}),
        },
        "mcp_servers": mcp_status,
    }


register("intelligence_status", SCHEMA, handler, toolset="core", requires_owner=True)
