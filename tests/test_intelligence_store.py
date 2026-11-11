import tempfile
import unittest
from pathlib import Path

from memory.store import Store


class IntelligenceStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(str(Path(self.tmp.name) / "memory.db"))

    def tearDown(self):
        self.store.conn.close()
        self.tmp.cleanup()

    def test_search_history_recovers_old_context_but_stays_in_chat_scope(self):
        self.store.append("chat-a", "user", "We chose PostgreSQL for the billing service")
        self.store.append("chat-b", "user", "We chose MongoDB for the billing service")
        hits = self.store.search_history("PostgreSQL billing", chat_id="chat-a")
        self.assertEqual(len(hits), 1)
        self.assertIn("PostgreSQL", hits[0]["content"])
        self.assertEqual(hits[0]["chat_id"], "chat-a")

    def test_search_history_can_skip_recent_turns_to_avoid_echo(self):
        self.store.append("chat-a", "user", "old architecture decision")
        self.store.append("chat-a", "assistant", "latest architecture decision")
        hits = self.store.search_history("architecture", chat_id="chat-a", exclude_recent=1)
        self.assertEqual([x["role"] for x in hits], ["user"])

    def test_research_sources_are_validated_deduplicated_and_searchable(self):
        first = self.store.record_source("chat-a", {
            "title": "LangChain install guide",
            "url": "https://docs.langchain.com/oss/python/langchain/install",
            "snippet": "Install the Python package with pip or uv.",
        })
        duplicate = self.store.record_source("chat-a", {
            "title": "LangChain install guide updated",
            "url": "https://docs.langchain.com/oss/python/langchain/install",
            "snippet": "Updated content",
        })
        self.assertTrue(first["ok"])
        self.assertTrue(duplicate["duplicate"])
        self.assertEqual(self.store.search_sources("chat-a", "LangChain install")[0]["title"],
                         "LangChain install guide updated")
        unsafe = self.store.record_source("chat-a", {"title": "bad", "url": "javascript:alert(1)"})
        self.assertFalse(unsafe["ok"])

    def test_journal_notes_are_scoped_and_retrievable(self):
        saved = self.store.save_journal_note("chat-a", "Project decision", "Use local FTS5 before vector memory", tags=["architecture"])
        self.assertTrue(saved["ok"])
        self.assertEqual(len(self.store.search_journal_notes("chat-a", "FTS5")), 1)
        self.assertEqual(self.store.search_journal_notes("chat-b", "FTS5"), [])

    def test_curated_tenant_library_is_shared_without_sharing_chat_transcripts(self):
        self.store.save_journal_note("chat-a", "Shared stack decision", "Use Graphify for source-code structure", scope="library")
        self.store.save_journal_note("chat-a", "Private chat note", "This remains within chat A only")
        in_b = self.store.search_journal_notes("chat-b", "Graphify", include_library=True)
        self.assertEqual(len(in_b), 1)
        self.assertEqual(in_b[0]["scope"], "library")
        self.assertEqual(self.store.search_journal_notes("chat-b", "Private chat", include_library=True), [])
        self.assertEqual(self.store.search_journal_notes("chat-b", "Graphify"), [])

    def test_library_scope_is_explicit_and_tenant_database_isolation_is_preserved(self):
        saved = self.store.save_journal_note("chat-a", "Team convention", "Prefer source citations", scope="library")
        self.assertTrue(saved["ok"])
        other = Store(str(Path(self.tmp.name) / "other-tenant.db"))
        try:
            self.assertEqual(other.search_journal_notes("chat-b", "source citations", include_library=True), [])
        finally:
            other.conn.close()
        invalid = self.store.save_journal_note("chat-a", "Invalid", "This is a long enough body", scope="all_users")
        self.assertFalse(invalid["ok"])

    def test_tool_metrics_track_success_failure_and_latency(self):
        self.store.record_tool_metric("web_search", True, 240)
        self.store.record_tool_metric("web_search", False, 760)
        stats = self.store.tool_stats()
        self.assertEqual(stats[0]["tool_name"], "web_search")
        self.assertEqual(stats[0]["calls"], 2)
        self.assertEqual(stats[0]["successes"], 1)
        self.assertEqual(stats[0]["failures"], 1)
        self.assertEqual(stats[0]["avg_ms"], 500)


if __name__ == "__main__":
    unittest.main()
