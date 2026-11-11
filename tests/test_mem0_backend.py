import unittest
from memory import mem0_backend


class Mem0BackendTests(unittest.TestCase):
    def test_disabled_by_default_without_attempting_provider_initialization(self):
        state = mem0_backend.status({"memory": {"mem0": {"enabled": False}}})
        self.assertFalse(state["enabled"])
        self.assertEqual(state["status"], "disabled")

    def test_tenant_user_ids_are_stable_and_not_raw_phone_numbers(self):
        a = mem0_backend._tenant_id("628123456789")
        b = mem0_backend._tenant_id("628123456789")
        c = mem0_backend._tenant_id("628123456780")
        self.assertEqual(a, b)
        self.assertNotEqual(a, c)
        self.assertNotIn("628123456789", a)

    def test_enabled_mem0_requires_explicit_provider_and_vector_configuration(self):
        state = mem0_backend.status({"memory": {"mem0": {"enabled": True}}})
        self.assertFalse(state["available"])
        self.assertEqual(state["status"], "incomplete_config")

    def test_semantic_memory_scope_includes_chat_id_and_metadata_drops_raw_chat_id(self):
        self.assertNotEqual(
            mem0_backend._tenant_id("tenant-a", "chat-1"),
            mem0_backend._tenant_id("tenant-a", "chat-2"),
        )
        safe = mem0_backend._safe_metadata({"kind": "fact", "source": "user", "chat_id": "private-room", "token": "secret"})
        self.assertEqual(safe, {"kind": "fact", "source": "user"})

    def test_semantic_memory_fails_closed_without_active_chat_context(self):
        result = mem0_backend.add("durable note", "tenant-a", config={}, chat_id="")
        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "missing_chat_context")
        result = mem0_backend.search("durable note", "tenant-a", config={}, chat_id="")
        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "missing_chat_context")


if __name__ == "__main__":
    unittest.main()
