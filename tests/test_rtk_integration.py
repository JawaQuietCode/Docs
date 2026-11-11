import subprocess
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from tools.rtk import rewrite_command


class RtkRewriteTests(unittest.TestCase):
    def setUp(self):
        from tools.rtk import _probe_token_killer
        _probe_token_killer.cache_clear()

    def _stat_patch(self):
        return patch("tools.rtk.os.stat", return_value=SimpleNamespace(st_mtime_ns=123, st_size=456))

    def test_rewrites_supported_command(self):
        verified = subprocess.CompletedProcess(args=["rtk", "gain"], returncode=0, stdout="", stderr="")
        result = subprocess.CompletedProcess(
            args=["rtk", "rewrite", "git status"], returncode=0,
            stdout="rtk git status\n", stderr="",
        )
        with patch("tools.rtk.shutil.which", return_value="/usr/bin/rtk"), self._stat_patch(), patch(
            "tools.rtk.subprocess.run", side_effect=[verified, result]
        ) as run:
            outcome = rewrite_command("git status", {"PATH": "/usr/bin"})
        self.assertEqual(outcome["command"], "rtk git status")
        self.assertTrue(outcome["applied"])
        self.assertEqual(outcome["engine"], "rtk")
        self.assertEqual(run.call_args_list[0].args[0], ["/usr/bin/rtk", "gain"])
        self.assertEqual(run.call_args_list[1].args[0], ["/usr/bin/rtk", "rewrite", "git status"])
        self.assertEqual(run.call_args_list[1].kwargs["env"]["RTK_TELEMETRY_DISABLED"], "1")

    def test_rtk_subprocess_does_not_receive_provider_secrets(self):
        verified = subprocess.CompletedProcess(args=["rtk", "gain"], returncode=0, stdout="", stderr="")
        result = subprocess.CompletedProcess(args=[], returncode=0, stdout="rtk git status", stderr="")
        env = {"PATH": "/usr/bin", "HOME": "/home/kwee", "LLM_API_KEY": "secret-provider-key"}
        with patch("tools.rtk.shutil.which", return_value="/usr/bin/rtk"), self._stat_patch(), patch(
            "tools.rtk.subprocess.run", side_effect=[verified, result]
        ) as run:
            rewrite_command("git status", env)
        for call in run.call_args_list:
            child_env = call.kwargs["env"]
            self.assertNotIn("LLM_API_KEY", child_env)
            self.assertEqual(child_env["PATH"], "/usr/bin")
            self.assertEqual(child_env["HOME"], "/home/kwee")

    def test_exit_one_keeps_original_command(self):
        verified = subprocess.CompletedProcess(args=["rtk", "gain"], returncode=0, stdout="", stderr="")
        result = subprocess.CompletedProcess(args=[], returncode=1, stdout="", stderr="")
        with patch("tools.rtk.shutil.which", return_value="/usr/bin/rtk"), self._stat_patch(), patch(
            "tools.rtk.subprocess.run", side_effect=[verified, result]
        ):
            outcome = rewrite_command("custom-command --flag", {"PATH": "/usr/bin"})
        self.assertEqual(outcome["command"], "custom-command --flag")
        self.assertFalse(outcome["applied"])
        self.assertFalse(outcome["blocked"])

    def test_undocumented_nonzero_exit_is_an_error_not_a_security_deny(self):
        verified = subprocess.CompletedProcess(args=["rtk", "gain"], returncode=0, stdout="", stderr="")
        result = subprocess.CompletedProcess(args=[], returncode=2, stdout="", stderr="unexpected status")
        with patch("tools.rtk.shutil.which", return_value="/usr/bin/rtk"), self._stat_patch(), patch(
            "tools.rtk.subprocess.run", side_effect=[verified, result]
        ):
            outcome = rewrite_command("git push", {"PATH": "/usr/bin"})
        self.assertFalse(outcome["blocked"])
        self.assertEqual(outcome["command"], "git push")
        self.assertEqual(outcome["reason"], "rtk_error")

    def test_missing_rtk_is_fail_open(self):
        with patch("tools.rtk.shutil.which", return_value=None):
            outcome = rewrite_command("git status", {"PATH": "/usr/bin"})
        self.assertEqual(outcome["command"], "git status")
        self.assertFalse(outcome["applied"])
        self.assertFalse(outcome["blocked"])
        self.assertEqual(outcome["reason"], "not_installed")

    def test_timeout_and_runtime_error_are_fail_open(self):
        verified = subprocess.CompletedProcess(args=["rtk", "gain"], returncode=0, stdout="", stderr="")
        with patch("tools.rtk.shutil.which", return_value="/usr/bin/rtk"), self._stat_patch(), patch(
            "tools.rtk.subprocess.run", side_effect=[verified, subprocess.TimeoutExpired("rtk rewrite", 2)]
        ):
            outcome = rewrite_command("git status", {"PATH": "/usr/bin"}, timeout=2)
        self.assertEqual(outcome["command"], "git status")
        self.assertEqual(outcome["reason"], "timeout")
        from tools.rtk import _probe_token_killer
        _probe_token_killer.cache_clear()
        with patch("tools.rtk.shutil.which", return_value="/usr/bin/rtk"), self._stat_patch(), patch(
            "tools.rtk.subprocess.run", side_effect=[verified, OSError("exec denied")]
        ):
            outcome = rewrite_command("git status", {"PATH": "/usr/bin"})
        self.assertEqual(outcome["command"], "git status")
        self.assertEqual(outcome["reason"], "runtime_error")

    def test_disabled_or_empty_command_does_not_spawn_rtk(self):
        with patch("tools.rtk.subprocess.run") as run:
            disabled = rewrite_command("git status", {"PATH": "/usr/bin"}, enabled=False)
            empty = rewrite_command("  ", {"PATH": "/usr/bin"})
        self.assertEqual(disabled["reason"], "disabled")
        self.assertEqual(empty["reason"], "empty_command")
        run.assert_not_called()


    def test_name_collision_wrong_rtk_binary_is_not_executed(self):
        wrong = subprocess.CompletedProcess(args=["rtk", "gain"], returncode=1, stdout="", stderr="unknown command")
        with patch("tools.rtk.shutil.which", return_value="/usr/bin/rtk"), self._stat_patch(), patch(
            "tools.rtk.subprocess.run", return_value=wrong
        ) as run:
            outcome = rewrite_command("git status", {"PATH": "/usr/bin"})
        self.assertEqual(outcome["reason"], "not_token_killer")
        self.assertEqual(outcome["command"], "git status")
        self.assertEqual(run.call_count, 1)

if __name__ == "__main__":
    unittest.main()
