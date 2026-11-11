from copy import deepcopy

from agent import main


def test_fanout_requires_registered_owner(monkeypatch, tmp_path):
    config = deepcopy(main.CONFIG)
    config["root"] = str(tmp_path)
    config["owner"] = ["628123456789"]
    config["primaryOwner"] = "628123456789"
    monkeypatch.setattr(main, "CONFIG", config)

    assert main._fanout_tool_config("628999999999@s.whatsapp.net", "chat-1") is None
    assert main._fanout_tool_config("", "chat-1") is None


def test_fanout_inherits_owner_tool_allowlist_and_tenant_memory(monkeypatch, tmp_path):
    config = deepcopy(main.CONFIG)
    config["root"] = str(tmp_path)
    config["owner"] = ["628123456789", "628987654321"]
    config["primaryOwner"] = "628123456789"
    monkeypatch.setattr(main, "CONFIG", config)

    tool_config = main._fanout_tool_config("628987654321@s.whatsapp.net", "chat-2")
    assert tool_config is not None
    context = tool_config["context"]
    assert context["tenant"] == "628987654321"
    assert context["chat_id"] == "chat-2"
    assert context["role"] == "owner"
    assert context["owner_confirmed"] is False
    assert "shell" in context["tool_allow"]
    assert context["memory_db_path"].endswith("data/tenants/628987654321/memory/kwee.db")
    assert tool_config["llm"]["apiKey"] in ("***", "")


def test_fanout_respects_owner_role_subagent_deny(monkeypatch, tmp_path):
    config = deepcopy(main.CONFIG)
    config["root"] = str(tmp_path)
    config["owner"] = ["628123456789", "628987654321"]
    config["primaryOwner"] = "628123456789"
    config.setdefault("security", {}).setdefault("roles", {})["owner"] = {
        "tools": ["*"], "deny": ["subagent"]
    }
    monkeypatch.setattr(main, "CONFIG", config)
    assert main._fanout_tool_config("628987654321@s.whatsapp.net", "chat-2") is None
