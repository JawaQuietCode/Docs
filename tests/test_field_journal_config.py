import unittest
from pathlib import Path
import yaml

ROOT = Path(__file__).resolve().parents[1]


class FieldJournalConfigTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.config = yaml.safe_load((ROOT / "config" / "default.yaml").read_text(encoding="utf-8"))

    def test_native_agent_is_default_so_native_skills_and_mcp_are_reachable(self):
        self.assertEqual(self.config.get("defaultMechanism"), "native")
        self.assertEqual((self.config.get("engine") or {}).get("mode"), "native")

    def test_github_mcp_is_read_only_and_requires_external_secret(self):
        github = self.config["mcp"]["servers"]["github"]
        self.assertTrue(github["read_only"])
        self.assertIn("GITHUB_PERSONAL_ACCESS_TOKEN", github["required_env"])
        self.assertIn("GITHUB_READ_ONLY", github["args"])
        self.assertTrue(github["allow_tools"])

    def test_mem0_stays_opt_in(self):
        self.assertFalse(self.config["memory"]["mem0"]["enabled"])


if __name__ == "__main__":
    unittest.main()
