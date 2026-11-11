from memory.store import Store
from tools.builtin import subagent


def test_peer_roles_have_distinct_grounded_instructions():
    researcher = subagent._role_prompt("researcher", "Check source claims")
    critic = subagent._role_prompt("critic", "Check source claims")
    assert "sumber primer" in researcher.lower()
    assert "kontra-contoh" in critic.lower()
    assert researcher != critic


def test_peer_learning_is_saved_to_tenant_library_with_provenance(tmp_path):
    db_path = tmp_path / "memory.sqlite3"
    result = subagent._remember_peer_result(
        {"memory_db_path": str(db_path), "chat_id": "chat-1", "tenant": "tenant-1"},
        role="mentor",
        title="Lesson about testing",
        content="A useful lesson: write the regression test before changing the implementation.",
    )
    assert result["ok"] is True
    assert result["scope"] == "library"
    store = Store(str(db_path))
    try:
        notes = store.search_journal_notes("chat-2", "regression test", include_library=True)
        assert len(notes) == 1
        assert "mentor" in notes[0]["tags"]
        assert "unverified" in notes[0]["tags"]
    finally:
        store.conn.close()


def test_peer_learning_does_not_store_secrets(tmp_path):
    result = subagent._remember_peer_result(
        {"memory_db_path": str(tmp_path / "memory.sqlite3"), "chat_id": "chat-1"},
        role="researcher",
        title="Provider setup",
        content="api_key=sk-12345678901234567890",
    )
    assert result["ok"] is False
    assert "rahasia" in result["error"]


def test_subagent_uses_trusted_runtime_key_not_redacted_tool_config(monkeypatch):
    from agent.config import CONFIG
    monkeypatch.setitem(CONFIG["llm"], "apiKey", "runtime-secret")
    monkeypatch.setitem(CONFIG["llm"], "baseUrl", "https://provider.example/v1")
    client = subagent._build_llm({"llm": {
        "apiKey": "***", "baseUrl": "https://provider.example/v1",
        "model": "model-from-env", "requestIntervalSeconds": 2,
    }})
    assert client.api_key == "runtime-secret"
    assert client.model == "model-from-env"
    assert client.request_interval_seconds == 2


def test_team_fanout_saves_shared_notes_for_each_peer(tmp_path, monkeypatch):
    def fake_run_one(goal, context, args, config):
        return {"ok": True, "agent": args.get("agent", "peer"), "result": f"lesson from {goal}"}

    monkeypatch.setattr(subagent, "_run_one", fake_run_one)
    db_path = tmp_path / "team.sqlite3"
    result = subagent.fanout([
        {"judul": "Research", "goal": "research topic", "agent": "researcher"},
        {"judul": "Review", "goal": "review design", "agent": "critic"},
    ], {"context": {"memory_db_path": str(db_path), "chat_id": "chat-1"}})
    assert result["ok"] is True
    assert [x["agent"] for x in result["tugas"]] == ["researcher", "critic"]
    assert all(x["memory"]["ok"] for x in result["tugas"])
    store = Store(str(db_path))
    try:
        notes = store.search_journal_notes("chat-2", "lesson from", include_library=True)
        assert len(notes) == 2
    finally:
        store.conn.close()


def test_peers_read_relevant_unverified_notes_from_shared_tenant_library(tmp_path):
    db_path = tmp_path / "peer-library.sqlite3"
    store = Store(str(db_path))
    try:
        store.save_journal_note(
            "chat-1", "Agent loop lesson",
            "A bounded agent loop needs a step limit and a regression test.",
            tags=["peer-learning", "mentor", "unverified"], scope="library",
        )
    finally:
        store.conn.close()
    context = subagent._peer_memory_context(
        "bounded agent loop step limit", 
        {"context": {"memory_db_path": str(db_path), "chat_id": "chat-2"}},
    )
    assert "UNVERIFIED DATA" in context
    assert "Agent loop lesson" in context
    assert "step limit" in context
