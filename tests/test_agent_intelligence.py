import unittest
from agent.intelligence import JOURNALISM_RULES, intelligence_context, rank_tools
from agent.context import tools_block
from memory.store import Store
import tempfile
from pathlib import Path


class AgentIntelligenceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(str(Path(self.tmp.name) / "memory.db"))

    def tearDown(self):
        self.store.conn.close()
        self.tmp.cleanup()

    def test_research_queries_prioritize_search_and_fetch_without_dropping_tools(self):
        tools = [{"name": "shell"}, {"name": "web_search"}, {"name": "web_fetch"}, {"name": "memory"}]
        ranked = rank_tools(tools, "research latest sources for the report")
        self.assertEqual([x["name"] for x in ranked[:2]], ["web_search", "web_fetch"])
        self.assertEqual({x["name"] for x in ranked}, {x["name"] for x in tools})

    def test_tool_learning_uses_measured_success_and_latency(self):
        tools = [{"name": "web_search"}, {"name": "exa_search"}]
        self.store.record_tool_metric("exa_search", True, 120)
        self.store.record_tool_metric("exa_search", True, 150)
        self.store.record_tool_metric("web_search", False, 12000)
        self.store.record_tool_metric("web_search", False, 14000)
        ranked = rank_tools(tools, "research latest facts", self.store)
        self.assertEqual(ranked[0]["name"], "exa_search")

    def test_context_retrieves_old_messages_sources_and_notes_but_not_other_chats(self):
        self.store.append("chat-a", "user", "The Graphify graph store uses AST nodes and local graph search")
        self.store.append("chat-b", "user", "Graphify means use remote hosted memory and private data")
        self.store.record_source("chat-a", {"title": "Graphify docs", "url": "https://github.com/Graphify-Labs/graphify", "snippet": "AST repository graph"})
        self.store.save_journal_note("chat-a", "Graphify architecture", "Prefer local graph exploration before vector search", tags=["graphify"])
        text = intelligence_context(self.store, "chat-a", "Tell me about Graphify architecture")
        self.assertIn("Graphify docs", text)
        self.assertIn("local graph exploration", text)
        self.assertNotIn("remote hosted memory", text)
        self.assertNotIn("remote hosted memory", text)
        self.assertIn("UNTRUSTED", text)

    def test_tool_prompt_preserves_adaptive_rank_order(self):
        schemas = [
            {"schema": {"name": "web_search"}},
            {"schema": {"name": "web_fetch"}},
            {"schema": {"name": "shell"}},
        ]
        prompt = tools_block(schemas)
        self.assertLess(prompt.index("web_search"), prompt.index("web_fetch"))
        self.assertLess(prompt.index("web_fetch"), prompt.index("shell"))

    def test_journalism_rules_require_provenance_and_no_fabricated_sources(self):
        self.assertIn("Never fabricate a citation", JOURNALISM_RULES)
        self.assertIn("primary sources", JOURNALISM_RULES)


if __name__ == "__main__":
    unittest.main()
