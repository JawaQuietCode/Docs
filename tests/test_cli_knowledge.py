import argparse
import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agent import cli


class KnowledgeCliTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def run_knowledge(self, action, payload=None):
        stdout = io.StringIO()
        args = argparse.Namespace(action=action, payload=json.dumps(payload or {}))
        with patch.object(cli, "ROOT", self.root), patch.object(cli, "_config", return_value={"root": str(self.root)}), contextlib.redirect_stdout(stdout):
            code = cli.cmd_knowledge(args)
        return code, json.loads(stdout.getvalue())

    def test_cli_status_and_claim_round_trip_are_executable(self):
        code, status = self.run_knowledge("status")
        self.assertEqual(code, 0)
        self.assertIn("native_deterministic", status)
        code, added = self.run_knowledge("claim_add", {
            "subject": "Kwee CLI", "predicate": "stores", "object": "knowledge claims",
            "source_label": "CLI smoke test",
        })
        self.assertEqual(code, 0)
        code, found = self.run_knowledge("claim_search", {"query": "Kwee CLI knowledge"})
        self.assertEqual(code, 0)
        self.assertEqual(found["count"], 1)
        self.assertEqual(found["claims"][0]["id"], added["id"])
        self.assertTrue((self.root / "data" / "cli" / "memory" / "knowledge.sqlite3").exists())

    def test_cli_rejects_non_object_json_and_bad_json(self):
        stdout = io.StringIO()
        args = argparse.Namespace(action="status", payload="[]")
        with patch.object(cli, "ROOT", self.root), patch.object(cli, "_config", return_value={"root": str(self.root)}), contextlib.redirect_stdout(stdout):
            code = cli.cmd_knowledge(args)
        self.assertEqual(code, 2)
        self.assertFalse(json.loads(stdout.getvalue())["ok"])

    def test_payload_cannot_select_an_external_database(self):
        outside = self.root / "outside.sqlite3"
        code, result = self.run_knowledge("claim_add", {
            "subject": "Kwee", "predicate": "uses", "object": "tenant-safe storage",
            "database_path": str(outside),
        })
        self.assertEqual(code, 0)
        self.assertFalse(outside.exists())
        self.assertTrue((self.root / "data" / "cli" / "memory" / "knowledge.sqlite3").exists())


if __name__ == "__main__":
    unittest.main()
