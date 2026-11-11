"""E2E native peer: real run_turn/tool dispatch/memory; provider response is deterministic."""
import json
from pathlib import Path

from agent.loop import run_turn
from memory.store import Store
from tools import registry
from tools.builtin import subagent


class ScriptedLLM:
    def __init__(self, mode):
        self.mode = mode
        self.calls = 0
        self.tool_result = ""

    def chat(self, messages, tools=None, timeout=None, **kwargs):
        self.calls += 1
        if self.mode == "parent" and self.calls == 1:
            return {
                "content": "",
                "tool_calls": [{
                    "id": "peer-call-1",
                    "type": "function",
                    "function": {
                        "name": "subagent",
                        "arguments": json.dumps({
                            "goal": "Ajarkan pola tes loop agent yang aman",
                            "agent": "mentor",
                            "tools": ["files"],
                            "remember": True,
                        }),
                    },
                }],
            }
        if self.mode == "parent":
            self.tool_result = " ".join(
                str(m.get("content") or "") for m in messages if m.get("role") == "tool"
            )
            return {"content": "Catatan peer disimpan untuk ditinjau.", "tool_calls": []}
        return {
            "content": "Gunakan batas iterasi eksplisit, simulasikan respons tool, dan uji jalur gagal sebelum perubahan.",
            "tool_calls": [],
        }


def test_native_peer_end_to_end_through_agent_loop_and_tenant_memory(tmp_path, monkeypatch):
    db = tmp_path / "peer-e2e.sqlite3"
    parent_llm = ScriptedLLM("parent")
    child_llm = ScriptedLLM("child")
    monkeypatch.setattr(subagent, "_build_llm", lambda config: child_llm)

    registry.discover()
    tool = registry.get("subagent")
    assert tool is not None
    config = {
        "context": {
            "memory_db_path": str(db),
            "tenant": "tenant-e2e",
            "chat_id": "conversation-a",
            "tool_allow": ["subagent", "files"],
        },
        "agent": {"subagentMaxIterations": 3, "timeoutSeconds": 8},
        "llm": {"model": "same-kwee-model", "baseUrl": "https://same-provider.invalid/v1"},
    }
    result = run_turn(
        llm=parent_llm,
        history=[{"role": "user", "content": "Berdiskusi dan simpan pelajaran tentang pengujian agent."}],
        tools=[tool],
        tool_config=config,
        max_iterations=3,
        timeout_seconds=8,
    )

    assert "Catatan peer disimpan" in result["raw"]
    assert "loop agent" in parent_llm.tool_result
    assert child_llm.calls == 1
    store = Store(str(db))
    try:
        notes = store.search_journal_notes("conversation-b", "batas iterasi simulasi tool", include_library=True)
        assert len(notes) == 1
        assert "mentor" in notes[0]["tags"]
        assert "peer-learning" in notes[0]["tags"]
        assert "unverified" in notes[0]["tags"]
        assert notes[0]["scope"] == "library"
    finally:
        store.conn.close()
