import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agent.llm import client
from memory.store import Store
from skills import loader
from tools.builtin.cron_tool import _is_direct_add_request


class RateLimitTests(unittest.TestCase):
    def test_pacer_waits_for_minimum_interval_across_same_provider(self):
        now = [100.0]
        sleeps = []

        def fake_sleep(seconds):
            sleeps.append(seconds)
            now[0] += seconds

        with patch.object(client.time, "monotonic", side_effect=lambda: now[0]), \
             patch.object(client.time, "sleep", side_effect=fake_sleep):
            client._RATE_LAST.pop("test-provider", None)
            self.assertEqual(client._pace_request("test-provider", 10), 0)
            self.assertEqual(client._pace_request("test-provider", 10), 10)
            self.assertEqual(sleeps, [10])

    def test_zero_interval_disables_pacing(self):
        self.assertEqual(client._pace_request("disabled-provider", 0), 0)


class MemoryTests(unittest.TestCase):
    def test_compaction_keeps_newest_summary_tail(self):
        with tempfile.TemporaryDirectory() as td:
            store = Store(str(Path(td) / "kwee.db"))
            store.set_profile("summary:chat", "OLD-" * 20)
            for i in range(4):
                store.append("chat", "user", f"message-{i}")
            result = store.compact("chat", protect_last_n=1,
                                   summarize=lambda msgs: "LATEST-VERIFIED-FACT", max_chars=32)
            self.assertTrue(result["compacted"])
            summary = store.get_profile()["summary:chat"]
            self.assertTrue(summary.endswith("LATEST-VERIFIED-FACT"))
            mode = store.conn.execute("PRAGMA journal_mode").fetchone()[0]
            self.assertEqual(mode.lower(), "wal")
            store.conn.close()


class ApprovalTests(unittest.TestCase):
    def test_explicit_schedule_request_does_not_need_second_approval(self):
        self.assertTrue(_is_direct_add_request("Jadwalkan agent setiap hari jam 08.00"))
        self.assertTrue(_is_direct_add_request("Buatkan cron untuk backup mingguan"))

    def test_ambiguous_request_does_not_auto_authorize_schedule(self):
        self.assertFalse(_is_direct_add_request("Apa saja jadwal yang tersedia?"))
        self.assertFalse(_is_direct_add_request("Tolong jelaskan cron"))


class SkillTelemetryTests(unittest.TestCase):
    def test_prompt_load_snapshot_lists_only_skills_injected(self):
        original = loader.EXTERNAL_DIR
        try:
            with tempfile.TemporaryDirectory() as td:
                root = Path(td)
                skill = root / "unit-demo"
                skill.mkdir()
                (skill / "SKILL.md").write_text(
                    "---\nname: unit-demo\ndescription: telemetry test\ntriggers: [telemetrytest]\n---\n# Demo skill\nloaded body", encoding="utf-8")
                loader.EXTERNAL_DIR = root
                loader.clear_cache()
                block = loader.skills_block("please telemetrytest")
                snap = loader.stats()["last_prompt_load"]
                self.assertEqual(snap["status"], "prepared")
                self.assertIn("unit-demo", snap["external"])
                self.assertTrue(loader.confirm_prompt_inclusion(block))
                self.assertEqual(loader.stats()["last_prompt_load"]["status"], "injected")
        finally:
            loader.EXTERNAL_DIR = original
            loader.clear_cache()


if __name__ == "__main__":
    unittest.main()
