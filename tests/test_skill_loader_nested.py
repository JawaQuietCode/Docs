from skills import loader


def test_discovers_skill_packages_at_arbitrary_depth(tmp_path, monkeypatch):
    external = tmp_path / "external"
    skill_dir = external / "ai-research" / "14-agents" / "crewai"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: crewai-deep\ndescription: Agent team orchestration\ntriggers: [agent team]\n---\n"
        "Use role-based agents and explicit handoffs.\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(loader, "EXTERNAL_DIR", external)
    loader.clear_cache()
    try:
        found = loader.get_skill("crewai-deep", include_hidden=False)
        assert found is not None
        assert found["name"] == "crewai-deep"
        assert found["path"] == str(skill_dir / "SKILL.md")
        assert found["security"]["verdict"] == "LOW_RISK"
    finally:
        loader.clear_cache()


def test_prompt_load_is_not_reported_as_injected_until_verified(tmp_path, monkeypatch):
    internal = tmp_path / "internal"
    external = tmp_path / "external"
    hidden = tmp_path / "hidden"
    for folder in (internal, external, hidden):
        folder.mkdir()
    (internal / "core.md").write_text(
        "---\nname: core-doctrine\ndescription: Core rules\n---\nKeep answers accurate.\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(loader, "INTERNAL_DIR", internal)
    monkeypatch.setattr(loader, "EXTERNAL_DIR", external)
    monkeypatch.setattr(loader, "HIDDEN_DIR", hidden)
    loader.clear_cache()
    try:
        block = loader.skills_block("ordinary request")
        assert loader.stats()["last_prompt_load"]["status"] == "prepared"
        assert loader.confirm_prompt_inclusion("system prefix\n" + block) is True
        assert loader.stats()["last_prompt_load"]["status"] == "injected"
        assert loader.confirm_prompt_inclusion("system prompt without skills") is False
        assert loader.stats()["last_prompt_load"]["status"] == "not_injected"
    finally:
        loader.clear_cache()


def test_failed_skill_build_cannot_be_overwritten_as_injected():
    loader.fail_prompt_load(RuntimeError("fixture"))
    assert loader.confirm_prompt_inclusion("a prompt without skill content") is False
    assert loader.stats()["last_prompt_load"]["status"] == "failed"


def test_reload_invalidates_previous_injected_status():
    loader.clear_cache()
    assert loader.stats()["load_visual"]["status"] == "not-yet-loaded"


def test_prompt_load_telemetry_is_isolated_per_chat(tmp_path, monkeypatch):
    internal = tmp_path / "internal"
    external = tmp_path / "external"
    hidden = tmp_path / "hidden"
    for folder in (internal, external, hidden):
        folder.mkdir()
    for name, trigger in (("alpha-peer", "alpha"), ("beta-peer", "beta")):
        folder = external / name
        folder.mkdir()
        (folder / "SKILL.md").write_text(
            f"---\nname: {name}\ndescription: {trigger}\ntriggers: [{trigger}]\n---\n"
            f"Skill body for {name}.\n",
            encoding="utf-8",
        )
    monkeypatch.setattr(loader, "INTERNAL_DIR", internal)
    monkeypatch.setattr(loader, "EXTERNAL_DIR", external)
    monkeypatch.setattr(loader, "HIDDEN_DIR", hidden)
    loader.clear_cache()
    try:
        alpha = loader.skills_block("alpha", request_id="tenant-a:chat-a")
        beta = loader.skills_block("beta", request_id="tenant-b:chat-b")
        assert loader.confirm_prompt_inclusion(alpha, request_id="tenant-a:chat-a")
        assert loader.confirm_prompt_inclusion(beta, request_id="tenant-b:chat-b")
        assert loader.stats(request_id="tenant-a:chat-a")["last_prompt_load"]["external"] == ["alpha-peer"]
        assert loader.stats(request_id="tenant-b:chat-b")["last_prompt_load"]["external"] == ["beta-peer"]
        assert "request_id" not in loader.stats(request_id="tenant-a:chat-a")["last_prompt_load"]
    finally:
        loader.clear_cache()
