import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tools.builtin import shell


class ShellRtkIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.config = {
            "root": str(self.root),
            "security": {
                "accessMode": "owner",
                "guardSelfKill": True,
                "allowAllExecutables": True,
                "execTimeoutSeconds": 5,
                "outputLimitBytes": 4096,
                "denyPaths": [],
                "rtk": {"enabled": True, "timeoutSeconds": 2},
            },
            "context": {},
        }

    def tearDown(self):
        self.tmp.cleanup()

    def test_shell_executes_rtk_rewritten_command_and_reports_it(self):
        optimized = {
            "command": "printf 'from-rtk'",
            "engine": "rtk", "available": True, "applied": True,
            "blocked": False, "reason": "rewritten", "detail": "",
        }
        with patch("tools.builtin.shell.rewrite_command", return_value=optimized) as rewrite:
            result = shell.handler({"command": "git status"}, self.config)
        self.assertEqual(result["exit_code"], 0)
        self.assertEqual(result["stdout"], "from-rtk")
        self.assertTrue(result["optimization"]["applied"])
        self.assertEqual(result["optimization"]["engine"], "rtk")
        self.assertEqual(rewrite.call_args.args[0], "git status")

    def test_rtk_failure_falls_back_to_the_original_under_kwee_policy(self):
        fallback = {
            "command": "git push", "engine": "rtk", "available": True, "applied": False,
            "blocked": False, "reason": "rtk_error", "detail": "unexpected status",
        }
        with patch("tools.builtin.shell.rewrite_command", return_value=fallback), patch(
            "tools.builtin.shell._run_command", return_value={"exit_code": 0, "stdout": "original", "stderr": ""}
        ) as run:
            result = shell.handler({"command": "git push"}, self.config)
        self.assertEqual(result["stdout"], "original")
        self.assertEqual(run.call_args.args[0], "git push")

    def test_rewritten_command_is_revalidated_against_deny_paths(self):
        optimized = {
            "command": "cat .env",
            "engine": "rtk", "available": True, "applied": True,
            "blocked": False, "reason": "rewritten", "detail": "",
        }
        self.config["security"]["denyPaths"] = [".env"]
        with patch("tools.builtin.shell.rewrite_command", return_value=optimized), patch(
            "tools.builtin.shell._run_command"
        ) as run:
            result = shell.handler({"command": "printf safe"}, self.config)
        self.assertIn("kebijakan keamanan", result["error"])
        self.assertEqual(result["optimization"]["reason"], "security_revalidation_failed")
        run.assert_not_called()

    def test_rtk_is_disabled_when_not_allowed_in_sandbox(self):
        self.config["security"].update({
            "accessMode": "sandbox", "allowAllExecutables": False,
            "allowedExecutables": ["printf"],
        })
        unchanged = {
            "command": "printf safe", "engine": "rtk", "available": False,
            "applied": False, "blocked": False, "reason": "disabled", "detail": "",
        }
        with patch("tools.builtin.shell.rewrite_command", return_value=unchanged) as rewrite:
            result = shell.handler({"command": "printf safe"}, self.config)
        self.assertEqual(result["stdout"], "safe")
        self.assertFalse(rewrite.call_args.kwargs["enabled"])


if __name__ == "__main__":
    unittest.main()
