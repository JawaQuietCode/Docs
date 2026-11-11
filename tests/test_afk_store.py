import tempfile
import unittest
from pathlib import Path

from agent.afk import AfkStore


class AfkStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = AfkStore(Path(self.tmp.name) / "kwee.db")

    def tearDown(self):
        self.tmp.cleanup()

    def test_start_persists_reason_and_returns_elapsed_fields(self):
        item = self.store.start("group-1", "user-a", "Alice", "tidur")
        self.assertEqual(item["reason"], "tidur")
        self.assertEqual(item["sender_id"], "user-a")
        self.assertGreaterEqual(item["started_at"], 0)

    def test_start_again_updates_existing_active_session(self):
        first = self.store.start("group-1", "user-a", "Alice", "tidur")
        second = self.store.start("group-1", "user-a", "Alice", "makan")
        self.assertEqual(first["id"], second["id"])
        self.assertEqual(second["started_at"], first["started_at"])
        self.assertEqual(second["reason"], "makan")
        self.assertEqual(len(self.store.list_active("group-1")), 1)

    def test_sessions_are_scoped_to_chat_and_stop_removes_active(self):
        self.store.start("group-1", "user-a", "Alice", "tidur")
        self.assertEqual(self.store.list_active("group-2"), [])
        ended = self.store.stop("group-1", "user-a")
        self.assertTrue(ended["ended_at"])
        self.assertEqual(self.store.list_active("group-1"), [])

    def test_read_token_is_scoped_and_expires(self):
        from agent.afk import create_read_token, verify_read_token
        token = create_read_token("group-1", "secret", now=100, ttl=30)
        self.assertTrue(verify_read_token("group-1", token, "secret", now=110))
        self.assertFalse(verify_read_token("group-2", token, "secret", now=110))
        self.assertFalse(verify_read_token("group-1", token, "secret", now=131))


if __name__ == "__main__":
    unittest.main()
