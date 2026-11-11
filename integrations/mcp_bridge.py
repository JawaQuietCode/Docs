"""Read-only MCP stdio bridge for explicitly configured Kwee servers.

The model can only invoke named tools exposed by a configured server allowlist.
The bridge never builds a shell command: subprocess arguments are passed as an
argv array to the official MCP SDK stdio transport.
"""
from __future__ import annotations

import asyncio
import hashlib
import importlib.util
import json
import os
import shutil
import threading
from contextlib import AsyncExitStack
from pathlib import Path
from typing import Any

ALLOWED_COMMANDS = {"docker", "python", "python3", "uv", "uvx", "npx"}
SAFE_PASSTHROUGH_ENV = {
    "GITHUB_PERSONAL_ACCESS_TOKEN", "GRAPHIFY_API_KEY", "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY", "GEMINI_API_KEY", "MOONSHOT_API_KEY",
    "PATH", "HOME", "TMPDIR", "TEMP", "LANG", "LC_ALL", "SYSTEMROOT",
    "PYTHONUSERBASE",
}
WRITE_TOOL_MARKERS = (
    "create", "update", "delete", "write", "push", "merge", "close",
    "reopen", "assign", "comment", "review", "submit", "dispatch",
    "approve", "fork", "star", "unstar", "enable", "disable", "cancel",
)


class MCPUnavailable(RuntimeError):
    pass


def is_read_only_tool(name: str, allowed: set[str] | list[str] | tuple[str, ...]) -> bool:
    """Strict allowlist plus a deny-list for common mutating verb prefixes."""
    tool = str(name or "").strip()
    if not tool or tool not in set(allowed or ()):
        return False
    low = tool.lower()
    return not any(
        low == marker or low.startswith(marker + "_") or ("_" + marker + "_") in low
        for marker in WRITE_TOOL_MARKERS
    )


def validate_server_config(config: dict) -> dict:
    """Validate a server config; this bridge intentionally supports stdio only."""
    if not isinstance(config, dict):
        raise ValueError("MCP server config must be an object")
    transport = str(config.get("transport") or "stdio").strip().lower()
    if transport != "stdio":
        raise ValueError("only local MCP stdio transport is enabled in this build")
    command = str(config.get("command") or "").strip()
    if Path(command).name not in ALLOWED_COMMANDS:
        raise ValueError(f"MCP command is not allowlisted: {command or '(empty)'}")
    args = config.get("args") or []
    if not isinstance(args, list) or len(args) > 100 or any(not isinstance(x, str) for x in args):
        raise ValueError("MCP args must be a list of at most 100 strings")
    allowed_tools = config.get("allow_tools") or config.get("allowTools") or []
    if not isinstance(allowed_tools, list) or not allowed_tools:
        raise ValueError("MCP server needs a non-empty explicit allow_tools list")
    if any(not isinstance(x, str) or not x.strip() for x in allowed_tools):
        raise ValueError("MCP allow_tools entries must be non-empty strings")
    if config.get("read_only", config.get("readOnly", True)) is not True:
        raise ValueError("this bridge only accepts read_only=true")
    pass_env = config.get("pass_env") or config.get("passEnv") or []
    if not isinstance(pass_env, list) or any(x not in SAFE_PASSTHROUGH_ENV for x in pass_env):
        raise ValueError("MCP pass_env contains an environment variable outside the safe allowlist")
    env = config.get("env") or {}
    if not isinstance(env, dict) or any(not isinstance(k, str) or not isinstance(v, (str, int, float, bool)) for k, v in env.items()):
        raise ValueError("MCP env must be a string-keyed scalar object")
    required_env = config.get("required_env") or config.get("requiredEnv") or []
    if not isinstance(required_env, list) or any(x not in SAFE_PASSTHROUGH_ENV for x in required_env):
        raise ValueError("MCP required_env contains an unsupported environment variable")
    required_files = config.get("required_files") or config.get("requiredFiles") or []
    if not isinstance(required_files, list) or any(not isinstance(x, str) for x in required_files):
        raise ValueError("MCP required_files must be a list of paths")
    return {
        **config,
        "transport": "stdio",
        "command": command,
        "args": list(args),
        "allow_tools": [str(x).strip() for x in allowed_tools],
        "pass_env": list(pass_env),
        "required_env": list(required_env),
        "required_files": list(required_files),
        "read_only": True,
    }


def _jsonable(value: Any):
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if hasattr(value, "model_dump"):
        try:
            return _jsonable(value.model_dump(mode="json"))
        except Exception:
            pass
    if hasattr(value, "dict"):
        try:
            return _jsonable(value.dict())
        except Exception:
            pass
    return str(value)


class MCPBridge:
    """Persistent event-loop-backed MCP client manager (one process per server)."""

    def __init__(self, timeout_seconds: int = 45):
        self.timeout_seconds = max(5, min(180, int(timeout_seconds or 45)))
        self._guard = threading.RLock()
        self._loop = None
        self._thread = None
        self._ready = threading.Event()
        self._states: dict[str, dict] = {}

    def _start_loop(self):
        with self._guard:
            if self._loop and self._loop.is_running():
                return
            self._ready.clear()
            loop = asyncio.new_event_loop()
            self._loop = loop

            def runner():
                asyncio.set_event_loop(loop)
                self._ready.set()
                loop.run_forever()
                pending = asyncio.all_tasks(loop)
                for task in pending:
                    task.cancel()
                if pending:
                    loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
                loop.close()

            self._thread = threading.Thread(target=runner, name="kwee-mcp-loop", daemon=True)
            self._thread.start()
        self._ready.wait(timeout=5)

    def _submit(self, coroutine, timeout: int | None = None):
        self._start_loop()
        future = asyncio.run_coroutine_threadsafe(coroutine, self._loop)
        try:
            return future.result(timeout=timeout or self.timeout_seconds)
        except Exception:
            future.cancel()
            raise

    @staticmethod
    def _root(config: dict | None) -> Path:
        return Path((config or {}).get("root") or ".").resolve()

    def server_status(self, name: str, server_config: dict, config: dict | None = None) -> dict:
        if not server_config.get("enabled", False):
            return {"name": name, "enabled": False, "available": False, "status": "disabled"}
        try:
            cfg = validate_server_config(server_config)
        except ValueError as exc:
            return {"name": name, "enabled": True, "available": False, "status": "invalid", "error": str(exc)}
        missing_env = [key for key in cfg["required_env"] if not os.environ.get(key)]
        if missing_env:
            return {"name": name, "enabled": True, "available": False, "status": "missing_env", "missing_env": missing_env}
        root = self._root(config)
        missing_files = []
        for raw in cfg["required_files"]:
            path = (root / raw).resolve()
            try:
                path.relative_to(root)
            except ValueError:
                return {"name": name, "enabled": True, "available": False, "status": "invalid", "error": "required file path escapes project root"}
            if not path.is_file():
                missing_files.append(raw)
        if missing_files:
            return {"name": name, "enabled": True, "available": False, "status": "missing_files", "missing_files": missing_files}
        if not shutil.which(Path(cfg["command"]).name):
            return {"name": name, "enabled": True, "available": False, "status": "missing_command", "command": cfg["command"]}
        return {"name": name, "enabled": True, "available": True, "status": "ready"}

    def _server_environment(self, cfg: dict) -> dict:
        env = {}
        # Minimal OS environment required to launch the configured binary.
        for key in ("PATH", "HOME", "TMPDIR", "TEMP", "LANG", "LC_ALL", "SYSTEMROOT", "PYTHONUSERBASE"):
            if os.environ.get(key):
                env[key] = os.environ[key]
        for key in cfg.get("pass_env", []):
            if os.environ.get(key):
                env[key] = os.environ[key]
        for key, value in (cfg.get("env") or {}).items():
            env[key] = str(value)
        return env

    async def _connect(self, name: str, cfg: dict, project_config: dict | None):
        try:
            from contextlib import AsyncExitStack
            from mcp import ClientSession, StdioServerParameters
            from mcp.client.stdio import stdio_client
        except Exception as exc:
            raise MCPUnavailable("Python MCP SDK belum terpasang; jalankan setup-intelligence.sh") from exc

        root = self._root(project_config)
        cwd_raw = cfg.get("cwd") or str(root)
        cwd = Path(cwd_raw)
        if not cwd.is_absolute():
            cwd = (root / cwd).resolve()
        try:
            cwd.relative_to(root)
        except ValueError as exc:
            raise ValueError("MCP cwd tidak boleh keluar dari project root") from exc
        if not cwd.is_dir():
            raise FileNotFoundError(f"MCP cwd tidak ditemukan: {cwd}")

        fingerprint_payload = {
            "command": cfg["command"], "args": cfg["args"], "cwd": str(cwd),
            "env": self._server_environment(cfg), "allow_tools": cfg["allow_tools"],
        }
        fingerprint = hashlib.sha256(json.dumps(fingerprint_payload, sort_keys=True).encode()).hexdigest()
        existing = self._states.get(name)
        if existing and existing.get("fingerprint") == fingerprint:
            return existing
        if existing:
            await existing["stack"].aclose()
            self._states.pop(name, None)

        params = StdioServerParameters(
            command=cfg["command"], args=cfg["args"], env=self._server_environment(cfg), cwd=str(cwd)
        )
        stack = AsyncExitStack()
        try:
            await stack.__aenter__()
            read_stream, write_stream = await stack.enter_async_context(stdio_client(params))
            session = await stack.enter_async_context(ClientSession(read_stream, write_stream))
            await session.initialize()
            state = {"stack": stack, "session": session, "fingerprint": fingerprint, "tools": None}
            self._states[name] = state
            return state
        except Exception:
            await stack.aclose()
            raise

    async def _list_tools_async(self, name: str, cfg: dict, project_config: dict | None):
        state = await self._connect(name, cfg, project_config)
        result = await state["session"].list_tools()
        tools = getattr(result, "tools", []) or []
        state["tools"] = tools
        allowed = set(cfg["allow_tools"])
        found = []
        for tool in tools:
            tname = str(getattr(tool, "name", "") or "")
            if not is_read_only_tool(tname, allowed):
                continue
            found.append({
                "name": tname,
                "description": str(getattr(tool, "description", "") or "")[:800],
                "input_schema": _jsonable(getattr(tool, "inputSchema", {}) or {}),
            })
        return found

    def list_tools(self, name: str, server_config: dict, config: dict | None = None) -> dict:
        cfg = validate_server_config(server_config)
        status = self.server_status(name, {**server_config, "enabled": True}, config)
        if not status.get("available"):
            return {"ok": False, "server": name, "status": status, "tools": []}
        try:
            tools = self._submit(self._list_tools_async(name, cfg, config))
            return {"ok": True, "server": name, "status": "connected", "tools": tools}
        except Exception as exc:
            return {"ok": False, "server": name, "status": "error", "error": str(exc)[:500], "tools": []}

    async def _call_tool_async(self, server: str, cfg: dict, tool_name: str,
                               arguments: dict, project_config: dict | None):
        state = await self._connect(server, cfg, project_config)
        if state.get("tools") is None:
            result = await state["session"].list_tools()
            state["tools"] = getattr(result, "tools", []) or []
        names = {str(getattr(x, "name", "") or "") for x in state["tools"]}
        if tool_name not in names or not is_read_only_tool(tool_name, cfg["allow_tools"]):
            return {"ok": False, "error": "tool MCP tidak diizinkan atau tidak tersedia"}
        response = await state["session"].call_tool(tool_name, arguments=arguments)
        contents = []
        for item in getattr(response, "content", []) or []:
            text = getattr(item, "text", None)
            if text is not None:
                contents.append({"type": "text", "text": str(text)[:12000]})
            else:
                contents.append(_jsonable(item))
        return {"ok": not bool(getattr(response, "isError", False)),
                "server": server, "tool": tool_name, "content": contents}

    def call_tool(self, server: str, server_config: dict, tool_name: str,
                  arguments: dict | None = None, config: dict | None = None) -> dict:
        cfg = validate_server_config(server_config)
        status = self.server_status(server, {**server_config, "enabled": True}, config)
        if not status.get("available"):
            return {"ok": False, "server": server, "status": status}
        if not is_read_only_tool(tool_name, cfg["allow_tools"]):
            return {"ok": False, "error": "tool tidak lolos allowlist read-only"}
        if not isinstance(arguments or {}, dict):
            return {"ok": False, "error": "arguments harus objek"}
        try:
            return self._submit(self._call_tool_async(server, cfg, tool_name, arguments or {}, config))
        except Exception as exc:
            return {"ok": False, "server": server, "tool": tool_name, "error": str(exc)[:500]}

    def close(self):
        if not self._loop or not self._loop.is_running():
            return
        async def _close_all():
            for state in list(self._states.values()):
                try:
                    await state["stack"].aclose()
                except Exception:
                    pass
            self._states.clear()
        try:
            asyncio.run_coroutine_threadsafe(_close_all(), self._loop).result(timeout=5)
        except Exception:
            pass
        self._loop.call_soon_threadsafe(self._loop.stop)


_DEFAULT_BRIDGE = MCPBridge()


def list_server_configs(config: dict | None = None) -> dict:
    mcp_cfg = (config or {}).get("mcp") or {}
    servers = mcp_cfg.get("servers") or {}
    if not isinstance(servers, dict):
        return {}
    return servers


def bridge_from_config(config: dict | None = None) -> MCPBridge:
    mcp_cfg = (config or {}).get("mcp") or {}
    timeout = mcp_cfg.get("timeout_seconds", mcp_cfg.get("timeoutSeconds", 45))
    if int(timeout or 45) != _DEFAULT_BRIDGE.timeout_seconds:
        _DEFAULT_BRIDGE.timeout_seconds = max(5, min(180, int(timeout or 45)))
    return _DEFAULT_BRIDGE
