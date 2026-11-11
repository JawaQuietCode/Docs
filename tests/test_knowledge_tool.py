import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tools import registry


class KnowledgeToolTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        from tools.builtin import knowledge_engine
        self.tool = registry.get("knowledge_engine")
        self.assertIsNotNone(self.tool)
        self.cfg = {"context": {"tenant_root": str(self.root), "tenant": "tenant-test", "tool_allow": ["knowledge_engine"]}}

    def tearDown(self):
        self.tmp.cleanup()

    def call_tool(self, args):
        return self.tool["handler"](args, self.cfg)

    def test_native_tool_persists_claim_and_provenance_in_tenant_root(self):
        added = self.call_tool({"action": "claim_add", "subject": "Kwee", "predicate": "runtime", "object": "Python", "source_uri": "https://example.org/source", "confidence": 0.8})
        self.assertTrue(added["ok"])
        found = self.call_tool({"action": "claim_search", "query": "Kwee runtime"})
        self.assertEqual(found["count"], 1)
        provenance = self.call_tool({"action": "claim_provenance", "claim_id": added["id"]})
        self.assertEqual(provenance["source"]["uri"], "https://example.org/source")
        self.assertTrue((self.root / "memory" / "knowledge.sqlite3").exists())

    def test_tool_does_not_open_database_outside_tenant_context(self):
        result = self.tool["handler"]({"action": "status"}, {})
        self.assertFalse(result["ok"])

    def test_native_scientific_workflow_via_tool_actions(self):
        project = self.call_tool({"action": "research_create", "title": "RTK test", "question": "Does RTK reduce model-visible output bytes?"})
        self.assertTrue(project["ok"], project)
        hypothesis = self.call_tool({"action": "hypothesis_add", "project_id": project["id"], "statement": "RTK reduces returned output bytes", "hypothesis_type": "descriptive", "alternatives": ["Commands are already short"], "predictions": ["Median returned bytes decrease"]})
        self.assertEqual(hypothesis["status"], "candidate")
        design = self.call_tool({"action": "study_design", "project_id": project["id"], "hypothesis_id": hypothesis["id"], "design_type": "paired benchmark", "independent_variables": ["RTK on/off"], "dependent_variables": ["output bytes"], "confounders": ["command family"], "controls": ["same command"]})
        self.assertTrue(design["ok"], design)
        observation = self.call_tool({"action": "observation_add", "project_id": project["id"], "hypothesis_id": hypothesis["id"], "observation": "A test command output was shortened", "source": "local benchmark"})
        self.assertEqual(observation["recorded_as"], "observation")
        report = self.call_tool({"action": "research_report", "project_id": project["id"]})
        self.assertEqual(len(report["hypotheses"]), 1)
        self.assertEqual(len(report["designs"]), 1)
        self.assertEqual(len(report["observations"]), 1)

    def test_conflicting_claims_and_revision_actions_are_reachable(self):
        first = self.call_tool({"action": "claim_add", "subject": "agent", "predicate": "model", "object": "model-a", "valid_from": "2026-01-01"})
        second = self.call_tool({"action": "claim_add", "subject": "agent", "predicate": "model", "object": "model-b", "valid_from": "2026-02-01"})
        conflict = self.call_tool({"action": "claim_reconcile", "subject": "agent", "predicate": "model"})
        self.assertEqual(len(conflict["conflicts"]), 1)
        revision = self.call_tool({"action": "claim_revise", "claim_id": first["id"], "new_object": "model-c", "reason": "New version evidence"})
        self.assertTrue(revision["ok"], revision)
        self.assertEqual(self.call_tool({"action": "claim_provenance", "claim_id": revision["id"]})["relations"][0]["relation"], "supersedes")

    def test_tool_rejects_non_tenant_custom_database_paths_by_design(self):
        outside = self.root / "outside.sqlite3"
        result = self.call_tool({"action": "claim_add", "subject": "x", "predicate": "y", "object": "z", "database_path": str(outside)})
        self.assertTrue(result["ok"])
        self.assertFalse(outside.exists())
        self.assertTrue((self.root / "memory" / "knowledge.sqlite3").exists())

    def test_unknown_action_and_invalid_claim_are_structured_errors(self):
        self.assertFalse(self.call_tool({"action": "claim_add", "subject": "", "predicate": "is", "object": "x"})["ok"])
        self.assertFalse(self.call_tool({"action": "invalid"})["ok"])

    def test_tool_is_reachable_from_agent_loop_e2e(self):
        from agent.loop import run_turn
        class FakeLLM:
            def __init__(self):
                self.calls = 0
                self.tool_payload = ""
            def chat(self, messages, tools=None, timeout=None, **kwargs):
                self.calls += 1
                if self.calls == 1:
                    return {"content": "", "tool_calls": [{"id": "knowledge-1", "type": "function", "function": {"name": "knowledge_engine", "arguments": json.dumps({"action": "claim_add", "subject": "Research", "predicate": "status", "object": "candidate", "claim_type": "hypothesis"})}}]}
                self.tool_payload = " ".join(str(m.get("content") or "") for m in messages if m.get("role") == "tool")
                return {"content": "Hipotesis disimpan sebagai kandidat.", "tool_calls": []}
        llm = FakeLLM()
        result = run_turn(llm, [{"role": "user", "content": "Simpan hipotesis"}], [self.tool], tool_config=self.cfg, max_iterations=3, timeout_seconds=8)
        self.assertIn("Hipotesis disimpan", result["raw"])
        self.assertIn("\"claim_type\": \"hypothesis\"", llm.tool_payload)
        self.assertEqual(self.call_tool({"action": "claim_search", "query": "Research status"})["count"], 1)


if __name__ == "__main__":
    unittest.main()
