"""Optional adapter exposing Kwee's native registry to LangChain.

Kwee's existing run_turn remains the default runtime; this module is an
interoperability seam, not a silent replacement of its permissions or loop.
"""
from __future__ import annotations

import inspect
from importlib import metadata
from typing import Any


def status() -> dict:
    try:
        from langchain_core.tools import StructuredTool  # noqa: F401
        version = metadata.version("langchain-core")
        return {"available": True, "package": "langchain", "core_version": version}
    except Exception:
        try:
            version = metadata.version("langchain")
            return {"available": True, "package": "langchain", "version": version}
        except Exception:
            return {"available": False, "package": "langchain", "hint": "jalankan setup-intelligence.sh"}


def _schema_model(name: str, schema: dict):
    from pydantic import Field, create_model

    params = (schema or {}).get("parameters") or {}
    props = params.get("properties") or {}
    required = set(params.get("required") or [])
    fields = {}
    mapping = {
        "string": str, "integer": int, "number": float, "boolean": bool,
        "object": dict, "array": list,
    }
    for field_name, spec in props.items():
        spec = spec if isinstance(spec, dict) else {}
        py_type = mapping.get(spec.get("type"), Any)
        description = str(spec.get("description") or "")
        default = ... if field_name in required else None
        fields[field_name] = (py_type, Field(default, description=description))
    model_name = "Kwee" + "".join(x.title() for x in name.split("_") if x) + "Args"
    return create_model(model_name, **fields)


def _invoke(handler, args: dict, config: dict | None):
    params = inspect.signature(handler).parameters
    if len(params) >= 2:
        return handler(args, config)
    return handler(args)


def to_langchain_tools(registry: list[dict], config: dict | None = None) -> list:
    """Convert allowed registry entries without executing them at conversion time."""
    try:
        from langchain_core.tools import StructuredTool
    except Exception:
        return []
    result = []
    for item in registry or []:
        name = str(item.get("name") or "").strip()
        handler = item.get("handler")
        schema = item.get("schema") or {}
        if not name or not callable(handler):
            continue
        args_schema = _schema_model(name, schema)

        def build_func(tool_handler, tool_name):
            def invoke(**kwargs):
                value = _invoke(tool_handler, kwargs, config)
                return value if isinstance(value, (str, dict, list)) else str(value)
            invoke.__name__ = tool_name
            return invoke

        result.append(StructuredTool.from_function(
            func=build_func(handler, name),
            name=name,
            description=str(schema.get("description") or name),
            args_schema=args_schema,
        ))
    return result
